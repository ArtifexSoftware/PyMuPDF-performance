import json
import os
import subprocess

import main


def run(command):
    main.log(f'Running: {command}')
    subprocess.run(command, shell=1, check=1)

def addpush_json(results, name, name_latest):
    '''
    Pushes new performance data as a JSON file to Github repository
    ArtifexSoftware/PyMuPDF-performance-results.

    We clone the results repository, and write `results` to a file called
    `name` using json.dump(). And we create/overwrite a softlink called
    `name_latest` that points to `name`.

    Then we use `git add`, `git commit` and `git push` to push the new results
    file and `results-latest` softlink to the results repository.

    Args:
        results:
            A dict containing the results.
        name:
            Name of results file.
        name_latest:
            Name of softlink to create that links to `name`.

    We requires environment variable PYMUPDF_PERFORMANCE_RESULTS_RW to be set
    to github access token. If not present, we return quietly.
    '''
    remote = f'git@github.com:ArtifexSoftware/PyMuPDF-performance-results'
    remote_leaf = os.path.basename(remote)

    gh_key = os.environ.get('PYMUPDF_PERFORMANCE_RESULTS_RW')
    if gh_key is None:
        main.log(f'Not pushing to ArtifexSoftware/PyMuPDF-performance because PYMUPDF_PERFORMANCE_RESULTS_RW not set')
        return

    ssh_id_path = os.path.abspath('ssh_id')

    try:
        # Write private key `gh_key` to file for use by git/ssh.
        #
        # Need to create file as read/write for current user only, so we have
        # to use `os.open()` instead of `open()`.
        #
        fd = os.open(ssh_id_path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_EXCL, 0o600)
        try:
            os.write(fd, gh_key.encode('utf8'))
        finally:
            os.close(fd)

        # This allows git commands to access the results repository.
        git_prefix = f'GIT_SSH_COMMAND="ssh -i {ssh_id_path}"'

        # Clone results repository.
        run(f'{git_prefix} git clone {remote}.git')
        run(f'cd {remote_leaf} && git config user.email "julian.smith@artifex.com"')
        run(f'cd {remote_leaf} && git config user.name "PyMuPDF-performance"')

        # Create new results file.
        with open(f'{remote_leaf}/{name}', 'w') as f:
            json.dump(results, f, indent='    ', sort_keys=1)

        # Create latest softlink.
        run(f'cd {remote_leaf} && ln -sf {name} {name_latest}')

        # Push to results repository.
        run(f'cd {remote_leaf} && git add {name} {name_latest}')
        run(f'cd {remote_leaf} && git commit -m "{name}: new performance results."')
        run(f'cd {remote_leaf} && {git_prefix} git push')

    finally:
        try:
            os.remove(ssh_id_path)
        except Exception:
            log(f'Ignoring exception removing {ssh_id_path}: {e}')

    main.log(f'Have pushed results to {remote}.')
    
