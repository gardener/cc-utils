import threading

'''
workaround bug in mako use lock to sequentialise invocations of mako.template.Template
see: https://github.com/sqlalchemy/mako/issues/378
'''
template_lock = threading.Lock()


def indent_func(depth):
    return lambda text: text.replace("\n", "\n" + depth * " ")
