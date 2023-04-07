#!/usr/bin/env python3

'''
Runs PDF Python library performance tests and generates JSON file.

If we are running in a Github workflow with an appropriate
key in the environment, the JSON file is pushed to
ArtifexSoftware/PyMuPDF-performance-results. See github.py for details.

JSON format:

    {
        'data': # List of dicts, one for each timed test run.
        [
            {
                'e': int,None,str   # 0 success, None timeout, non-zero error code, string exception text.
                'path': str         # Name of input PDF file.
                't': float          # Elapsed time.
                'testname': str     # E.g. 'render' or 'text'.
                'toolname': str     # E.g. 'pymupdf' or 'poppler'.
            },
            ...
        ]
        'toolversions':
        {
            # `version` is whatever is returned by the
            # `get_version_<toolname>()` function. Typically a string, or tuple
            # etc.
            #
            toolname:str: version
        }
        'platform':
        {
            # Various items from the `platform` module, such as:
            'processor':                'amd64'
            'python_implementation':    'CPython'
            'python_version':           '3.9.16'
            'system':                   'OpenBSD'
            ...
        }
        'date': 1680704072.1528542
    }

Args:

    --internal-check 0|1

        If 1, we don't run performance fns, instead pretending each one took 1
        second. Used to check the code.

    --mupdf <mupdf-location>

        Set location of MuPDF when building PyMuPDF. Similar to --pymupdf. Internally
        we set PYMUPDF_SETUP_MUPDF_BUILD to <mupdf-location>.

    --path <path>
        Add <path> to list of paths to test; can be specified multiple
        times. If not specified, we test with all input files.

    --mupdfpy <mupdfpy-location>
    --pymupdf <pymupdf-location>

        Set location of PyMuPDF. If specified, we build PyMuPDF and install
        into the venv; otherwise we install PyMuPDF from pypi.org.

        If location starts with `git:`, the remaining text is used in a git
        clone command, for example:

            --pymupdf 'git:--branch master https://github.com/ArtifexSoftware/PyMuPDF.git'

        Otherwise location is a directory on local machine (typically a
        checkout of PyMuPDF).

        If <...-location> is '' or '0', we don't use mupdfpy/pymupdf at all.

    --pymupdf-build 0|1
        If 0, do not rebuild PyMuPDF. Default is 1.

    --timeout <timeout>
        Set fixed timeout for all tests. Otherwise we use hard-coded variable
        timeouts.

    --venv-install 0|1

        If 0 we assume the venv is already set up; this can save a few seconds
        on startup. Otherwise (the default) we create it, upgrade pip, and
        install various PDF libraries.

'''

import json
import multiprocessing
import os
import platform
import re
import subprocess
import sys
import time

import github


def performance(tests=None, paths=None, tools=None, timeout=None, internal_check=None, pymupdfs=None):
    '''
    Runs performance tests and saves to JSON results file whose name contains
    current date/time.
    
    Args:
        paths:
            If None we test with all input files. Otherwise should be a list of
            files to test with.
        timeout:
            Fixed timeout for all tests. If None we use default timeouts.
        internal_check:
            If true we don't actually run tests but instead pretend that all
            timings are 1.
        pymupdfs:
            Dict mapping PyMuPDF build identifiers such as 'mupdf-master', to
            directory containing installation's `fitz/` directory.
    '''
    time_now = time.time()

    log(f'PyMuPDF variants are:')
    for n, v in pymupdfs.items():
        log(f'    {n}: {v}')

    # Input files.
    #
    if paths:
        pathnames = paths
    else:
        pathnames = []
        for leaf in [
                'DB-Systems.pdf',
                'PyMuPDF.pdf',
                'adobe.pdf',
                'artifex-website.pdf',
                'chinese-example.pdf',
                'fontforge.pdf',
                'pandas.pdf',
                'pythonbook.pdf',
                'sample-50-MB-pdf-file.pdf',
                ]:
            path = os.path.relpath( os.path.abspath( f'{__file__}/../{leaf}'))
            pathnames.append( path)

    # Find all do_<testname>_<toolname>() functions, and derive all test names
    # and tool names.
    #
    testnames = set(tests) if tests else set()
    toolnames = set(tools) if tools else set()

    # Define functions that call pymupdf variants.
    #
    new_globals = dict()
    _make_pymupdf_version_variants(pymupdfs, new_globals)
    for fnname, fn in globals().items():
        match = re.match(f'^do_([a-z]+)_([a-z0-9_]+)$', fnname)
        if match:
            testname = match.group(1)
            toolname = match.group(2)
            if not tests:
                testnames.add(testname)
            if toolname == 'pymupdf':
                _make_pymupdf_variants(fnname, fn, pymupdfs, new_globals, None if tools else toolnames)
            elif not tools:
                toolnames.add(toolname)

    for n, v in new_globals.items():
        #log(f'Setting globals({n}) to {v}')
        globals()[n] = v

    # Set up results dict.
    #
    results = dict()
    results['toolversions'] = dict()
    results['data'] = list()
    results['date'] = dict()
    results['date']['seconds'] = time_now
    results['date']['string'] = time.strftime("%Y-%m-%d-%H-%M", time.gmtime( time_now))

    # Find platform info. We use all items in the `platform` module that are
    # callable with no parameters. We exclude items whose names start with '_'
    # or whose values cannot be serialised by json.dumps().
    #
    results['platform'] = dict()
    for name, v in platform.__dict__.items():
        if name.startswith('_'):
            continue
        try:
            value = v()
            _ = json.dumps(value)
        except Exception:
            continue
        results['platform'][name] = value
        #log(f'Setting results["platform"]["{name}"] to: {value!r}')

    # Find tool versions.
    #
    for toolname in toolnames:
        name = f'get_version_{toolname}'
        toolversions_fn = globals().get(name)
        if not toolversions_fn:
            raise Exception(f'Need function {name}() to find version of {toolname=}.')
        if toolname.startswith('pymupdf'):
            # We must not import this pymupdf because it will make it
            # impossible to import a different pymupdf variant later on. So we
            # use `multiprocessing`, receiving the version via a queue.
            #
            queue = multiprocessing.Queue()
            p = multiprocessing.Process(target=toolversions_fn, args=(queue,))
            p.start()
            version = queue.get()
            p.join()
            log('Have called pymupdf version fn. {version=}')
        else:
            version = toolversions_fn()
        results['toolversions'][toolname] = version

    log(f'testnames:\n{json.dumps(list(testnames), indent="    ", sort_keys=1)}')
    log(f'toolnames:\n{json.dumps(list(toolnames), indent="    ", sort_keys=1)}')
    log(f'pathnames:\n{json.dumps(pathnames, indent="    ", sort_keys=1)}')
    log(f'toolversions:\n{json.dumps(results["toolversions"], indent="    ", sort_keys=1)}')

    def all_tests():
        '''
        Yields `(testname, path, toolname, fn)` for each test to run.
        '''
        for testname in sorted(testnames):
            for path in pathnames:
                for toolname in toolnames:
                    fn = globals().get(f'do_{testname}_{toolname}')
                    if fn:
                        yield testname, path, toolname, fn

    # Run performance tests.
    #
    num_tests = 0
    for _ in all_tests():
        num_tests += 1
    i = 0
    for testname, path, toolname, fn in all_tests():
        i += 1
        log(f'### {i}/{num_tests}: {testname=} {path=} {toolname=} {fn.__name__=}')
        if internal_check:
            t, e = 1, 0
        else:
            if timeout:
                timeout2 = timeout
            elif 0 and fn.__name__ == 'do_copy_pypdf2':
                timeout2 = 600
            elif 0 and fn.__name__ == 'do_render_pdf2jpg':
                timeout2 = 600
            else:
                timeout2 = 300
            t, e = time_it(lambda : fn(path), timeout2)
        log(f'### {i}/{num_tests}: {testname=} {path=} {toolname=} {fn.__name__=}: {t=} {e=}')
        result = dict(
                testname=testname,
                path=path,
                toolname=toolname,
                t=t,
                e=e,
                )
        results['data'].append(result)

    # Show results.
    #
    log(f'results:\n{json.dumps(results, indent="    ", sort_keys=1)}')

    if tests or paths or tools or internal_check:
        name_prefix = 'internal_results'
    else:
        name_prefix = 'results'
    name = f'{name_prefix}-{time.strftime("%Y-%m-%d-%H-%M", time.gmtime( time_now))}.json'
    name_latest = f'{name_prefix}-latest.json'
    
    # Push results to Github results repository.
    #
    github.addpush_json(results, name, name_latest)

    # Save results locally.
    #
    name2 = os.path.relpath( os.path.abspath( f'{__file__}/../{name}'))
    name_latest2 = os.path.relpath( os.path.abspath( f'{__file__}/../{name_latest}'))
    with open(name2, 'w') as f:
        json.dump(results, f, indent='    ', sort_keys=1)
    log(f'Have written results to: {name2}')
    try:
        os.remove(name_latest2)
    except Exception:
        pass
    os.symlink(name, name_latest2)
    log(f'Have created symlink: {name_latest} -> {name}')


def time_it(fn, timeout):
    '''
    Runs `fn()` in a separate process using Python's `multiprocessing`
    module.
    
    Returns (t, e). `t` is the time in seconds to run fn(). `e` is 0 on
    success, None if timeout, non-zero error code or string exception text.
    '''
    p = multiprocessing.Process(target=fn)
    t0 = time.perf_counter()
    p.start()
    p.join(timeout)
    if p.exitcode is None:
        # Timeout.
        log(f'Multiprocessing timeout.')
        e = None
        p.terminate()
        p.join(10)
        if p.exitcode is None:
            p.kill()
            p.join(10)
            if p.exitcode is None:
                raise Exception(f'Cannot terminate multiprocess running {fn.__name__}')
    else:
        e = p.exitcode
    t = time.perf_counter() - t0
    return t, e


def _import_pymupdf(install):
    '''
    Imports `fitz` from directory `install` by temporarily modifying
    `sys.path`.
    '''
    #log(f'_import_pymupdf(): {install=}')
    sys.path.insert(0, install)
    try:
        import fitz
        #log(f'Have imported fitz. {install=} {fitz.__file__=}')
        assert fitz.__file__.startswith(install), f'Failed to import fitz from {install}: {fitz.__file__=}'
    finally:
        del sys.path[0]


def _make_pymupdf_version_variants(pymupdfs, new_globals):
    '''
    Creates `get_version_pymupdf_<variant>()` functions which return PyMuPDF
    version.

    As of 2023-02-21 these functions are not actually called.
    '''
    for variant, install_dir in pymupdfs.items():
        fnname2 = f'get_version_pymupdf_{variant}'
        def fn2(queue, install_dir=install_dir):
            log(f'_make_pymupdf_version_variants():fn2(): {variant=} {install_dir=}')
            _import_pymupdf(install_dir)
            version = get_version_pymupdf()
            queue.put(version)
        new_globals[fnname2] = fn2
        

def _make_pymupdf_variants(fnname, fn, pymupdfs, new_globals, toolnames):
    '''
    Creates `<fnname>_<variant>()` functions which import a specific build of
    PyMuPDF and then call `fn()`. These functions generally need to be run in a
    different process, because re-import of a different PyMuPDF module does not
    work.
    '''
    #log(f'_make_pymupdf_variants(): {fnname=} {fn=}')
    for variant, install_dir in pymupdfs.items():
        fnname2 = f'{fnname}_{variant}'
        # For reasons i don't understand, we need to pass `install_dir` as a
        # default param here, otherwise fn2() can see the wrong value.
        def fn2(path, install_dir=install_dir):
            #log(f'_make_pymupdf_variants():fn2(): {variant=} {install_dir=} {fn2=} {fn=}')
            _import_pymupdf(install_dir)
            fn(path)
        assert fnname2 not in new_globals
        new_globals[fnname2] = fn2
        #log(f'_make_pymupdf_variants(): have added {fnname2=} {fn2=}')
        if toolnames:
            toolnames.add(f'pymupdf_{variant}')
        

# Tool version functions.
#
# There must be one of these for each tool. Should return anything that can be
# serialised by json. Should also import anything that the tool's performance
# functions will use, to reduce startup delays when timing.
#

def get_version_pymupdf():
    # Returns a dict with verion information, including detailed git
    # information about PyMuPDF and MuPDF.
    #
    import fitz
    log(f'get_version_pymupdf(): {fitz.__file__=} {fitz.version=}')
    pymupdf_version = fitz.version

    pymupdf_git_sha     = getattr(fitz, 'pymupdf_git_sha', None)
    pymupdf_git_comment = getattr(fitz, 'pymupdf_git_comment', None)
    pymupdf_git_diff    = getattr(fitz, 'pymupdf_git_diff', None)
    pymupdf_git_branch  = getattr(fitz, 'pymupdf_git_branch', None)

    mupdf_git_sha       = getattr(fitz, 'mupdf_git_sha', None)
    mupdf_git_comment   = getattr(fitz, 'mupdf_git_comment', None)
    mupdf_git_diff      = getattr(fitz, 'mupdf_git_diff', None)
    mupdf_git_branch    = getattr(fitz, 'mupdf_git_branch', None)
    
    mupdf_version = fitz.mupdf_version_tuple
    return dict(
            pymupdf=pymupdf_version,

            pymupdf_git_sha=pymupdf_git_sha,
            pymupdf_git_comment=pymupdf_git_comment,
            #pymupdf_git_diff=pymupdf_git_diff,
            pymupdf_git_branch=pymupdf_git_branch,

            mupdf_git_sha=mupdf_git_sha,
            mupdf_git_comment=mupdf_git_comment,
            #mupdf_git_diff=mupdf_git_diff,
            mupdf_git_branch=mupdf_git_branch,
            
            )

def get_version_pdfrw():
    import pdfrw
    return pdfrw.__version__

def get_version_pikepdf():
    import pikepdf
    return pikepdf.__version__

def get_version_pypdf2():
    import PyPDF2
    return PyPDF2.__version__

def get_version_pdf2jpg():
    import pdf2jpg.pdf2jpg
    return None

def get_version_pdfminer():
    import pdfminer
    return pdfminer.__version__

def get_version_poppler():
    cp = subprocess.run('pdftotext -v', shell=1, check=1, capture_output=1, text=1)
    return cp.stdout + cp.stderr

def get_version_pypdfium2():
    return None


# Performance test functions.
#
# Functions should be called `do_<testname>_<toolname>()`.
#
# Each of these functions is passed a single `path` arg, the PDF file to
# process.
#

# do_copy_*()
#

def do_copy_pdfrw(path):
    import pdfrw
    doc = pdfrw.PdfReader(path)
    writer = pdfrw.PdfWriter()
    writer.trailer = doc
    writer.write(f'{path}.copy.pdfrw')

def do_copy_pikepdf(path):
    import pikepdf
    doc = pikepdf.open(path)
    doc.save(f'{path}.copy.pike')

def do_copy_pymupdf(path):
    import fitz
    doc = fitz.open(path)
    doc.save(f'{path}.copy.pymupdf')

def do_copy_pypdf2(path):
    import PyPDF2
    pdfmerge = PyPDF2.PdfMerger()
    pdfmerge.append(path)
    pdfmerge.write(f'{path}.copy.pypdf2')
    pdfmerge.close()

def do_copy_pypdfium2(path):
    import pypdfium2
    doc = pypdfium2.PdfDocument(path)
    doc.save(f'{path}.copy.pypdfium2')
    

# do_render_*()
#

def do_render_pdf2jpg(path):
    import pdf2jpg.pdf2jpg
    outdir = f'{path}.render.pdf2jpg-images'
    os.makedirs(outdir, exist_ok=1)
    if not pdf2jpg.pdf2jpg.convert_pdf2jpg(path, outdir, pages='ALL', dpi=150):
        return 1

def do_render_poppler(path):
    command = f'pdftoppm -r 150 -png {path} {path}.render.poppler-image'
    print(f'Running: {command}')
    subprocess.run(command, shell=1, check=1)

def do_render_pymupdf(path):
    import fitz
    doc = fitz.open(path)
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        out = f'{path}.render.pymupdf-image-{page.number}.png'
        pix.save(out)
        log(f'Have written to: {out}')
        pix = None
    doc.close()

def do_render_pypdfium2(path):
    import pypdfium2
    doc = pypdfium2.PdfDocument(path)
    for i in range(len(doc)):
        page = doc[i]
        bitmap = page.render(scale=150 / 72)
        img = bitmap.to_pil()
        out = f'{path}.render.pypdfium2-image-{i}.png'
        img.save(out)
        log(f'Have written to: {out}')
    doc.close()


# do_text_*()
#

def do_text_pdfminer(path):
    import pdfminer.high_level
    pdfminer.high_level.extract_text(path)

def do_text_poppler(path):
    subprocess.run(f'pdftotext {path} {path}.text.poppler', shell=1, check=1)

def do_text_pymupdf(path):
    import fitz
    doc = fitz.open(path)
    for page in doc:
        page.get_text()

def do_text_pypdf2(path):
    import PyPDF2
    reader = PyPDF2.PdfReader(path)
    for page in reader.pages:
        page.extract_text()

def do_text_pypdfium2(path):
    import pypdfium2
    doc = pypdfium2.PdfDocument(path)
    for page in doc:
        page.get_textpage().get_text_range()
    doc.close()


# Other
#

def log(text):
    print(text)
    sys.stdout.flush()


def pymupdf_install(pymupdf_location, mupdf_location, root, local_git_dir):
    '''
    Builds and installs PyMuPDF using pip.

    pymupdf_location:
        Path of PyMuPDF directory in which to build PyMuPDF.
        
        Or git location, similar to PyMuPDF/setup.py's PYMUPDF_SETUP_MUPDF_BUILD,
        e.g.: git:--branch master https://github.com/ArtifexSoftware/PyMupDF.git

    mupdf_location:
        If not None, is used for PyMuPDF/setup.py's PYMUPDF_SETUP_MUPDF_BUILD,
        e.g.: git:--branch master https://github.com/ArtifexSoftware/mupdf.git

    root:
        Directory into which we install.
    '''
    if not pymupdf_location:
        return

    git_prefix = 'git:'
    if pymupdf_location.startswith(git_prefix):
        command_suffix = pymupdf_location[len(git_prefix):]
        pymupdf_location = local_git_dir or 'PyMuPDF'
        
        command = f'git clone'
        command += f' --depth 1'
        command += f' {command_suffix}'
        command += f' {pymupdf_location}'
        log(f'Running: {command}')
        subprocess.run(command, shell=1, check=1)

        # Show sha of checkout.
        command = f'cd {pymupdf_location} && git show --pretty=oneline|head -n 1'
        log( f'Running: {command}')
        sys.stdout.flush()
        subprocess.run( command, shell=1, check=0)

    assert os.path.isdir(pymupdf_location)

    # Build PyMuPDF.
    env = ''
    if mupdf_location:
        env = 'PYMUPDF_SETUP_MUPDF_TGZ= '
        if mupdf_location.startswith('git:'):
            env += f'PYMUPDF_SETUP_MUPDF_BUILD="{mupdf_location}"'
        else:
            env += f'PYMUPDF_SETUP_MUPDF_BUILD="{os.path.relpath(mupdf_location, pymupdf_location)}"'
    if platform.system() == 'OpenBSD':
        # Need to use system clang-python and swig because they are not
        # available in pypi.org and building from sdist fails.
        command = f'cd {pymupdf_location} && {env} python3 setup.py install'
        if root:
            command = f'{command} --root {os.path.relpath(root, pymupdf_location)}'
    else:
        command = f'cd {pymupdf_location} && {env} pip install -v .'
        if root:
            # This creates `<root>/fitz/{fitz.py,...}`.
            command = f'{command} --upgrade --target {os.path.relpath(root, pymupdf_location)}'
    
    log( f'Running: {command}')
    subprocess.run( command, shell=1, check=1)


if __name__ == '__main__':

    venv_install = True
    internal_check = False
    do = None
    mupdf_master_location = 'git:--branch master https://github.com/ArtifexSoftware/mupdf.git'
    mupdf_branch_location = 'git:--branch 1.21.x https://github.com/ArtifexSoftware/mupdf.git'
    pymupdf_location = 'git:--branch 1.21 https://github.com/pymupdf/PyMuPDF.git'
    mupdfpy_location = 'git:https://github.com/ArtifexSoftware/mupdfpy-julian.git'
    pymupdf_build = True
    timeout = None
    tests = []
    paths = []
    tools = []
    args = iter(sys.argv[1:])
    while 1:
        try:
            arg = next(args)
        except StopIteration:
            break

        if arg == '-h' or arg == '--help':
            log(__doc__)
            sys.exit()

        elif arg == '--internal-check':
            internal_check = int(next(args))

        elif arg == '--mupdf-branch':
            mupdf_branch_location = next(args)

        elif arg == '--mupdf-master':
            mupdf_master_location = next(args)

        elif arg == '--path':
            paths.append(next(args))

        elif arg == '--pymupdf':
            pymupdf_location = next(args)

        elif arg == '--mupdfpy':
            mupdfpy_location = next(args)

        elif arg == '--pymupdf-build':
            pymupdf_build = int(next(args))

        elif arg == '--timeout':
            timeout = float(next(args))

        elif arg == '--tool':
            tools.append(next(args))

        elif arg == '--test':
            tests.append(next(args))

        elif arg == '--venv-install':
            venv_install = int(next(args))
        else:
            raise Exception(f'Unrecognised {arg=}')

    if sys.base_prefix == sys.prefix:
        # We are not inside a venv. Re-run ourselves inside a venv so that we
        # can use pypi.org packages such as pypdf2.
        log(f'Re-running inside a venv.')
        command = 'true'
        # Install required system packages.
        if platform.system() == 'Linux':
            command += f' && sudo apt install poppler-utils'
        elif platform.system() == 'OpenBSD':
            command += f' && sudo pkg_add poppler-utils'
        # Create venv.
        if venv_install:
            command += f' && {sys.executable} -m venv pylocal'
        # Activate the venv.
        command += f' && . pylocal/bin/activate'
        # Install Python packages from pypi.org.
        if venv_install:
            command += f' && python -m pip install --upgrade pip'
            command += f' && python -m pip install --upgrade pypdf2 pdfminer.six pdfrw pikepdf pdf2jpg pypdfium2'
            if not pymupdf_location:
                command += ' && python -m pip install --upgrade pymupdf'
        # Rerun ourselves inside the venv.
        command += f' && python {" ".join(sys.argv)}'
        log(f'Running: {command}')
        subprocess.run(command, check=True, shell=1)
        sys.exit()

    else:
        pymupdfs = dict()
        if pymupdf_location == '0':
            pymupdf_location = None
        if mupdfpy_location == '0':
            mupdfpy_location = None
        if mupdf_master_location == '0':
            mupdf_master_location = None
        if mupdf_branch_location == '0':
            mupdf_branch_location = None

        if pymupdf_location and mupdf_master_location:
            location = os.path.abspath(f'{__file__}/../install-pymupdf-mupdf-master')
            if pymupdf_build:
                pymupdf_install(pymupdf_location, mupdf_master_location, location, 'PyMuPDF-mupdf-master')
            pymupdfs['mupdf_master'] = location

        if pymupdf_location and mupdf_branch_location:
            location = os.path.abspath(f'{__file__}/../install-pymupdf-mupdf-branch')
            if pymupdf_build:
                pymupdf_install(pymupdf_location, mupdf_branch_location, location, 'PyMuPDF-mupdf-branch')
            pymupdfs['mupdf_branch'] = location
        
        if mupdfpy_location and mupdf_master_location:
            location = os.path.abspath(f'{__file__}/../install-mupdfpy-mupdf-master')
            if pymupdf_build:
                pymupdf_install(mupdfpy_location, mupdf_master_location, location, 'mupdfpy-mupdf-master')
            pymupdfs['mupdfpy_mupdf_master'] = location

        performance(
                tests=tests,
                paths=paths,
                tools=tools,
                timeout=timeout,
                internal_check=internal_check,
                pymupdfs=pymupdfs,
                )
