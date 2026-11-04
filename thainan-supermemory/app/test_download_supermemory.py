#!/usr/bin/env python3
"""
Testes unitários para download_supermemory.py (patch do binário e suporte a cache).
"""

import io
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from download_supermemory import (
    ORIGINAL_TARGET,
    PATCHED_TARGET,
    check_cache_and_patch,
    patch_binary,
)


class TestSupermemoryPatch(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.temp_dir.name, "supermemory-server")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_patch_fake_file_target_to_patched_same_size(self):
        initial_data = b"HEAD_BINARY_DATA\x00\xff" + ORIGINAL_TARGET + b"\x01\x02TAIL_BINARY_DATA"
        with open(self.fake_bin, "wb") as f:
            f.write(initial_data)

        size_before = os.path.getsize(self.fake_bin)
        res = patch_binary(self.fake_bin)

        self.assertTrue(res)
        self.assertTrue(os.path.exists(self.fake_bin))

        with open(self.fake_bin, "rb") as f:
            patched_data = f.read()

        size_after = os.path.getsize(self.fake_bin)
        self.assertEqual(size_before, size_after, "O tamanho do binário deve ser rigorosamente igual após o patch")
        self.assertNotIn(ORIGINAL_TARGET, patched_data, "Target original não deve mais existir no arquivo")
        self.assertIn(PATCHED_TARGET, patched_data, "Target patchado deve estar presente no arquivo")

        mode = stat.S_IMODE(os.stat(self.fake_bin).st_mode)
        self.assertEqual(mode, 0o755, "Permissão do arquivo deve ser mantida como 0755")

    def test_patch_idempotent(self):
        initial_data = b"HEAD_" + ORIGINAL_TARGET + b"_TAIL"
        with open(self.fake_bin, "wb") as f:
            f.write(initial_data)

        # Primeiro patch
        res1 = patch_binary(self.fake_bin)
        self.assertTrue(res1)
        with open(self.fake_bin, "rb") as f:
            data_after_first = f.read()

        # Segundo patch no arquivo já patchado
        res2 = patch_binary(self.fake_bin)
        self.assertTrue(res2)
        with open(self.fake_bin, "rb") as f:
            data_after_second = f.read()

        self.assertEqual(data_after_first, data_after_second, "Patch subsequente deve ser idempotente")
        self.assertIn(PATCHED_TARGET, data_after_second)

    def test_patch_missing_target_logs_warning_and_returns_false(self):
        initial_data = b"BINARY_WITHOUT_TARGETS\x00\x01\x02"
        with open(self.fake_bin, "wb") as f:
            f.write(initial_data)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            res = patch_binary(self.fake_bin)

        self.assertFalse(res)
        with open(self.fake_bin, "rb") as f:
            data_after = f.read()
        self.assertEqual(initial_data, data_after, "Arquivo não deve ser modificado se o alvo não existir")
        warning_msg = stderr_buf.getvalue()
        self.assertIn("AVISO", warning_msg)

    def test_patch_nonexistent_file(self):
        nonexistent = os.path.join(self.temp_dir.name, "does_not_exist")
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            res = patch_binary(nonexistent)
        self.assertFalse(res)

    def test_check_cache_and_patch_with_existing_unpatched_and_patched(self):
        # 1. Arquivo não existente -> False
        self.assertFalse(check_cache_and_patch(self.fake_bin))

        # 2. Arquivo vazio -> False
        with open(self.fake_bin, "wb") as f:
            pass
        self.assertFalse(check_cache_and_patch(self.fake_bin))

        # 3. Arquivo com o alvo não patchado -> aplica patch e retorna True
        with open(self.fake_bin, "wb") as f:
            f.write(b"ABC_" + ORIGINAL_TARGET + b"_XYZ")
        self.assertTrue(check_cache_and_patch(self.fake_bin))
        with open(self.fake_bin, "rb") as f:
            content = f.read()
        self.assertIn(PATCHED_TARGET, content)

        # 4. Arquivo já patchado em cache -> retorna True
        self.assertTrue(check_cache_and_patch(self.fake_bin))


if __name__ == "__main__":
    unittest.main()
