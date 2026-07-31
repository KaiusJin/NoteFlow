import unittest

from noteflow_worker.cache.content_addressed import content_hash


class ContentAddressedCacheTest(unittest.TestCase):
    def test_content_hash_has_framed_parts_and_is_deterministic(self):
        self.assertNotEqual(content_hash("ab", "c"), content_hash("a", "bc"))
        self.assertEqual(content_hash("same"), content_hash(b"same"))
