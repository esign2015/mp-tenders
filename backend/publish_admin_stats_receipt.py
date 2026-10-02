"""Publish only the stats completion receipt on the latest Git tree."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

PATH = 'data/admin_stats_schedule.json'


def git(*args, data=None, env=None):
    return subprocess.run(['git', *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env).stdout


def main():
    local = json.loads(Path(PATH).read_text())
    for _ in range(3):
        git('fetch', 'origin', 'main')
        parent = git('rev-parse', 'origin/main').decode().strip()
        try:
            old = git('show', parent + ':' + PATH)
            remote = json.loads(old)
        except (subprocess.CalledProcessError, ValueError):
            old, remote = b'', {'completed': {}}
        days = {key.split('@')[0] for key in local['completed']}
        merged = {key: value for key, value in remote.get('completed', {}).items() if key.split('@')[0] in days}
        merged.update(local['completed'])
        raw = (json.dumps({'completed': merged}, indent=2) + '\n').encode()
        if raw == old:
            print('Stats receipt already saved.')
            return
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'))
            git('read-tree', parent, env=env)
            blob = git('hash-object', '-w', '--stdin', data=raw).decode().strip()
            git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + PATH, env=env)
            tree = git('write-tree', env=env).decode().strip()
            commit = git('commit-tree', tree, '-p', parent, data=b'Update admin count refresh receipt [skip ci]\n').decode().strip()
        try:
            git('push', 'origin', commit + ':refs/heads/main')
            print('Stats receipt saved.')
            return
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError('Stats receipt could not be published; retry the counts job.')


if __name__ == '__main__':
    main()
