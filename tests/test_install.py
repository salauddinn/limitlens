import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestInstaller(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "install.sh supports only macOS and Linux")
    def test_existing_install_migrates_to_pypi(self):
        """An existing install is replaced directly from PyPI."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            calls = temp / "pipx-calls"
            fake_pipx = fake_bin / "pipx"
            fake_pipx.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$PIPX_CALLS\"\n"
                "if [[ \"$1\" == list ]]; then\n"
                "  printf 'package limitlens 1.0, installed using Python 3\\n'\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_pipx.chmod(fake_pipx.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env.update({
                "HOME": str(temp / "home"),
                "OSTYPE": "linux-gnu",
                "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                "PIPX_CALLS": str(calls),
            })
            result = subprocess.run(
                ["bash", str(PROJECT_ROOT / "install.sh")],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            commands = calls.read_text(encoding="utf-8").splitlines()
            self.assertIn("list", commands)
            self.assertIn("install --force limitlens", commands)
            self.assertNotIn("uninstall limitlens", commands)
            self.assertNotIn("install limitlens", commands)
            self.assertNotIn("upgrade limitlens", commands)
            self.assertNotIn("reinstall limitlens", commands)


if __name__ == "__main__":
    unittest.main()
