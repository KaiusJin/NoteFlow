import unittest
from unittest.mock import patch

from noteflow_worker.runtime.sandbox import initialize_parse_worker_sandbox


class ParseSandboxTest(unittest.TestCase):
    def test_parse_worker_applies_resource_limits_and_private_umask(self):
        with (
            patch("noteflow_worker.runtime.sandbox.resource.getrlimit", return_value=(1024, 4096)),
            patch("noteflow_worker.runtime.sandbox.resource.setrlimit") as set_limit,
            patch("noteflow_worker.runtime.sandbox.os.umask") as umask,
        ):
            initialize_parse_worker_sandbox()
        self.assertEqual(3, set_limit.call_count)
        umask.assert_called_once_with(0o077)
