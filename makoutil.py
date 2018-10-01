def indent_func(depth):
  return lambda text: text.replace("\n", "\n" + depth * " ")
