#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2020 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import json
import yaml
import sys


def main():
  if len(sys.argv) > 1:
    fh = open(sys.argv[1])
  else:
    fh = sys.stdin
  json.dump(yaml.load(fh, Loader=yaml.SafeLoader), sys.stdout)


if __name__ == '__main__':
  main()
