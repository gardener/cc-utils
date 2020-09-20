# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import MagicMock
import types
import sys
import os

from test._test_utils import capture_out
import kube.ctx
from ci.util import Failure

kube_ctx = kube.ctx.Ctx()


class CtxTest(unittest.TestCase):
    def setUp(self):
        self.examinee = kube_ctx
        self.fixture_ctx = types.SimpleNamespace()
        self.fixture_args = types.SimpleNamespace()
        self.fixture_ctx.args = self.fixture_args
        kube.ctx.global_ctx = lambda: self.fixture_ctx
        self.kubernetes_config_mock = MagicMock()
        kube.ctx.config = self.kubernetes_config_mock

    def test_get_kubecfg_cli_arg_should_have_precedence(self):
        # kubeconfig specified via CLI should have precedence over env var
        os.environ = {'KUBECONFIG': 'should_be_ignored'}
        self.fixture_args.kubeconfig = sys.executable

        self.examinee.get_kubecfg()
        self.kubernetes_config_mock.load_kube_config.assert_called_with(sys.executable)

    def test_get_kubecfg_env_should_be_honoured(self):
        os.environ = {'KUBECONFIG': sys.executable}

        self.examinee.get_kubecfg()

        self.kubernetes_config_mock.load_kube_config.assert_called_with(sys.executable)

    def test_get_kubecfg_should_fail_on_absent_file(self):
        self.fixture_args.kubeconfig = 'no such file'

        # silence output (we do not care about it, though)
        with capture_out() as (stdout, stderr):
            with self.assertRaises(Failure):
                self.examinee.get_kubecfg()
