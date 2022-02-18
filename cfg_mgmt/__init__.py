'''
utils for management of configuration provisioned in CICD-Clusters
(cfg and secrets stored and maintained in secret/private "cfg-repositories", replicated to
"secrets-server")
'''

import typing

revert_function = typing.Callable[[], None]
