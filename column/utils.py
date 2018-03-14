# Copyright (c) 2017 VMware, Inc. All Rights Reserved.
# SPDX-License-Identifier: GPL-3.0

import os

from ansible import cli
from ansible import constants
from ansible import errors
from ansible.parsing import dataloader
from ansible.parsing import vault
from ansible import release
from six.moves import configparser


ANSIBLE_CFG = os.path.join(os.sep, 'etc', 'ansible', 'ansible.cfg')
VAULT_PWD_FILE = os.path.join(os.sep, 'etc', 'column', 'vault_pass.txt')
DEFAULTS = {
    'vault_password_file': VAULT_PWD_FILE
}


def _get_vault_password_file():
    if os.path.exists(ANSIBLE_CFG):
        cfg = configparser.ConfigParser(DEFAULTS)
        cfg.read(ANSIBLE_CFG)
        return cfg.get('defaults', 'vault_password_file')


def _get_vault_lib():
    loader = dataloader.DataLoader()
    vault_ids = constants.DEFAULT_VAULT_IDENTITY_LIST

    vault_secrets = cli.CLI.setup_vault_secrets(loader, vault_ids=vault_ids,
                            vault_password_files=[_get_vault_password_file()],
                            ask_vault_pass=False,
                            auto_prompt=False)
    return vault.VaultLib(secrets=vault_secrets)


def vault_decrypt(value):
    this_vault = _get_vault_lib()
    try:
        return this_vault.decrypt(value)
    except errors.AnsibleError:
        return None


def vault_encrypt(value):
    this_vault = _get_vault_lib()
    return this_vault.encrypt(value)


def ansible_version():
    return release.__version__
