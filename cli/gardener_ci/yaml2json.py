#!/usr/bin/env python3

import json
import yaml
import sys


def main():
  if len(sys.argv) > 1:
    fh = open(sys.argv[1])
  else:
    fh = sys.stdin
  json.dump(yaml.load(fh, Loader=yaml.SafeLoader), sys.stdout, default=str)


if __name__ == '__main__':
  main()
