"""Exercise deployment selection without a Docker daemon or touching user services."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class UpgradeTests(unittest.TestCase):
    def test_preserves_named_and_bind_storage(self):
        source=Path(__file__).resolve().parents[1]
        for override in ('','docker-compose.storage.yml','docker-compose.istoreos.yml'):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve(); release=root/'release'; target=root/'existing'; binpath=root/'bin'
                release.mkdir();target.mkdir();binpath.mkdir()
                for part in ('hub','static','scripts'):
                    (target/part).mkdir();(release/part).mkdir()
                shutil.copy(source/'scripts/upgrade.sh',release/'scripts/upgrade.sh')
                for name in ('Dockerfile','docker-compose.yml','docker-compose.storage.yml','docker-compose.istoreos.yml'):
                    shutil.copy(source/name,release/name);(target/name).write_text('existing '+name)
                (target/'.env').write_text('ADMIN_PASSWORD=example-only\n')
                fake=binpath/'docker'
                fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_ROOT/calls"
case "$*" in
 'ps '*) echo test-container;;
 *com.docker.compose.project.working_dir*) echo "$TEST_TARGET";;
 *com.docker.compose.project.config_files*) echo "$TEST_TARGET/docker-compose.yml$TEST_OVERRIDE";;
 *Mounts*) echo /existing/storage;;
 *'cat /tmp/upgrade.sqlite3'*) echo sqlite-test-fixture;;
 *'.Image'*) echo old-image;;
 *'.State.Health'*) echo healthy;;
 *'ps -q hub'*) echo test-container;;
esac
''');fake.chmod(0o755)
                env=dict(os.environ,PATH=str(binpath)+os.pathsep+os.environ['PATH'],TEST_ROOT=str(root),TEST_TARGET=str(target),TEST_OVERRIDE=','+str(target/override) if override else '')
                result=subprocess.run(['sh',str(release/'scripts/upgrade.sh'),str(target)],env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr+result.stdout)
                self.assertEqual((target/'.env').read_text(),'ADMIN_PASSWORD=example-only\n')
                if override:self.assertEqual((target/override).read_text(),'existing '+override)
                compose=[line for line in (root/'calls').read_text().splitlines() if line.startswith('compose ')]
                for line in compose:
                    self.assertEqual(' -f docker-compose.storage.yml' in line,override=='docker-compose.storage.yml')
                    self.assertEqual(' -f docker-compose.istoreos.yml' in line,override=='docker-compose.istoreos.yml')
                self.assertTrue(list((target/'backups').glob('*/hub.sqlite3')))
