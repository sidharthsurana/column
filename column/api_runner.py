# Copyright (c) 2017 VMware, Inc. All Rights Reserved.
# SPDX-License-Identifier: GPL-3.0

import logging
import os

from ansible import cli
from ansible import constants
from ansible.executor import playbook_executor
from ansible.executor import task_queue_manager
from ansible.inventory.manager import InventoryManager
from ansible.parsing import dataloader
from ansible.parsing.splitter import parse_kv
from ansible.playbook import play
from ansible.plugins.loader import get_all_plugin_loaders
from ansible.vars.manager import VariableManager
import six

from column import callback
from column import exceptions
from column import runner

LOG = logging.getLogger(__name__)


class Namespace(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class ErrorsCallback(callback.AnsibleCallback):
    def __init__(self, *args, **kwargs):
        super(self.__class__, self).__init__(*args, **kwargs)
        self.failed_results = []

    def run_on_runner_failed(self, result, ignore_errors=False):
        if ignore_errors:
            # We only collect non-ignored errors
            return
        self.failed_results.append(result)


class APIRunner(runner.Runner):

    def __init__(self, inventory_file=None, **kwargs):
        super(self.__class__, self).__init__(inventory_file, **kwargs)
        self._callbacks = []
        self.tqm = None

    def get_progress(self):
        for c in self._callbacks:
            if c.__class__.__name__ == 'AnsibleTrackProgress':
                return c.progress
        return None

    def run_playbook(self, playbook_file, inventory_file=None, **kwargs):
        reload(constants)

        if not os.path.isfile(playbook_file):
            raise exceptions.FileNotFound(name=playbook_file)

        if inventory_file is None:
            inventory_file = self.inventory_file

        LOG.debug('Running with inventory file: %s', inventory_file)
        LOG.debug('Running with playbook file: %s', playbook_file)

        conn_pass = None
        if 'conn_pass' in kwargs:
            conn_pass = kwargs['conn_pass']

        become_pass = None
        if 'become_pass' in kwargs:
            become_pass = kwargs['become_pass']

        passwords = {'conn_pass': conn_pass, 'become_pass': become_pass}

        playbooks = [playbook_file]

        options = self._build_opt_dict(inventory_file, **kwargs)

        loader = dataloader.DataLoader()
        inventory = InventoryManager(loader=loader, sources=options.inventory)

        # create the variable manager, which will be shared throughout
        # the code, ensuring a consistent view of global variables
        variable_manager = VariableManager(loader=loader, inventory=inventory)
        options.extra_vars = {six.u(key): six.u(value)
                              for key, value in options.extra_vars.items()}
        variable_manager.extra_vars = cli.load_extra_vars(loader, options)
        inventory.subset(options.subset)
        pbex = playbook_executor.PlaybookExecutor(
            playbooks=playbooks,
            inventory=inventory,
            variable_manager=variable_manager,
            loader=loader,
            options=options,
            passwords=passwords)
        self.tqm = pbex._tqm
        errors_callback = ErrorsCallback()
        self.add_callback(errors_callback)
        # There is no public API for adding callbacks, hence we use a private
        # property to add callbacks
        pbex._tqm._callback_plugins.extend(self._callbacks)

        status = pbex.run()
        stats = pbex._tqm._stats
        failed_results = errors_callback.failed_results
        result = self._process_stats(stats, failed_results)
        return result

    def run_module(self, module_name='ping', module_args=None, hosts="all",
                   inventory_file=None, **kwargs):

        if not module_args:
            check_raw = module_name in ('command', 'win_command', 'shell',
                                        'win_shell', 'script', 'raw')
            module_args = parse_kv(constants.DEFAULT_MODULE_ARGS, check_raw)

        conn_pass = None
        if 'conn_pass' in kwargs:
            conn_pass = kwargs['conn_pass']

        become_pass = None
        if 'become_pass' in kwargs:
            become_pass = kwargs['become_pass']

        passwords = {'conn_pass': conn_pass, 'become_pass': become_pass}

        options = self._build_opt_dict(inventory_file, **kwargs)
        # dynamically load any plugins
        get_all_plugin_loaders()

        loader = dataloader.DataLoader()
        inventory = InventoryManager(loader=loader, sources=options.inventory)

        # create the variable manager, which will be shared throughout
        # the code, ensuring a consistent view of global variables
        variable_manager = VariableManager(loader=loader, inventory=inventory)
        options.extra_vars = {six.u(key): six.u(value) for key, value in
                              options.extra_vars.items()}
        variable_manager.extra_vars = cli.load_extra_vars(loader, options)

        inventory.subset(options.subset)

        play_ds = self._play_ds(hosts, module_name, module_args)
        play_obj = play.Play().load(play_ds, variable_manager=variable_manager,
                                    loader=loader)

        try:
            tqm = task_queue_manager.TaskQueueManager(
                inventory=inventory,
                variable_manager=variable_manager,
                loader=loader,
                options=options,
                passwords=passwords,
                stdout_callback='minimal',
                run_additional_callbacks=True
            )

            # There is no public API for adding callbacks, hence we use a
            # private property to add callbacks
            tqm._callback_plugins.extend(self._callbacks)

            result = tqm.run(play_obj)
        finally:
            if tqm:
                tqm.cleanup()
            if loader:
                loader.cleanup_all_tmp_files()

        stats = tqm._stats
        result = self._process_stats(stats)
        return result

    def add_callback(self, callback):
        self._callbacks.append(callback)

    def _build_opt_dict(self, inventory_file, **kwargs):
        args = {
            'check': None, 'listtasks': None, 'listhosts': None,
            'listtags': None, 'syntax': None, 'module_path': None,
            'skip_tags': [], 'ssh_common_args': '',
            'sftp_extra_args': '', 'scp_extra_args': '',
            'ssh_extra_args': '', 'become': constants.DEFAULT_BECOME,
            'become_user': constants.DEFAULT_BECOME_USER,
            'become_ask_pass': constants.DEFAULT_BECOME_ASK_PASS,
            'become_method': constants.DEFAULT_BECOME_METHOD,
            'forks': constants.DEFAULT_FORKS,
            'inventory': inventory_file,
            'private_key_file': constants.DEFAULT_PRIVATE_KEY_FILE,
            'extra_vars': {}, 'subset': constants.DEFAULT_SUBSET,
            'tags': [], 'verbosity': 0,
            'connection': constants.DEFAULT_TRANSPORT,
            'timeout': constants.DEFAULT_TIMEOUT,
            'diff': constants.DIFF_ALWAYS
        }
        args.update(self.custom_opts)
        args.update(kwargs)
        # In ansible 2.2, tags can be a string or a list, but only a list
        # is supported in 2.3.
        if isinstance(args['tags'], str):
            args['tags'] = args['tags'].split(',')
        elif not isinstance(args['tags'], list):
            raise exceptions.InvalidParameter(name=type(args['tags']).__name__,
                                              param='tag')
        return Namespace(**args)

    def _play_ds(self, hosts, module_name, module_args):
        return dict(
            name="Ansible module runner",
            hosts=hosts,
            gather_facts='no',
            tasks=[dict(action=dict(module=module_name, args=module_args),
                        async=0,
                        poll=constants.DEFAULT_POLL_INTERVAL)]
        )

    @staticmethod
    def _process_stats(stats, failed_results=[]):
        unreachable_hosts = sorted(stats.dark.keys())
        failed_hosts = sorted(stats.failures.keys())
        error_msg = ''
        failed_tasks = []
        if len(unreachable_hosts) > 0:
            tmpl = "Following nodes were unreachable: {0}\n"
            error_msg += tmpl.format(unreachable_hosts)
        for result in failed_results:
            task_name, msg, host = APIRunner._process_task_result(result)
            failed_tasks.append(task_name)
            tmpl = 'Task "{0}" failed on host "{1}" with message: {2}'
            error_msg += tmpl.format(task_name, host, msg)

        return {"error_msg": error_msg, "unreachable_hosts": unreachable_hosts,
                "failed_hosts": failed_hosts, 'failed_tasks': failed_tasks}

    @staticmethod
    def _process_task_result(task):
        result = task._result
        task_obj = task._task
        host = task._host
        if isinstance(result, dict) and 'msg' in result:
            error_msg = result.get('msg')
        else:
            # task result may be an object with multiple results
            msgs = []
            for res in result.get('results', []):
                if isinstance(res, dict) and 'msg' in res:
                    msgs.append(res.get('result'))
            error_msg = ' '.join(msgs)

        return task_obj.get_name(), error_msg, host.get_name()
