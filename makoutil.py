# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

def indent_func(depth):
  return lambda text: text.replace("\n", "\n" + depth * " ")
