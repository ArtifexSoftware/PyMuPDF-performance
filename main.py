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

    --austin <austin>
        Run everything via austin profiler; `austin` should be the austin
        executable, e.g. ./austin-3.5.0-gnu-linux-amd64/austin
    
    --build-check 0|1
        If 0 (the default), build failures are ignored.
    
    --cprofile 0|1
        If 1, profile individual test runs with cProfile.

    --perf 0|1
        If 1, run with `perf record`.
    
    --internal-check 0|1

        If 1, we don't run performance fns, instead pretending each one took 1
        second. Used to check the code.

    --mupdf-branch <location>
    --mupdf-master <location>
    --mupdfpy <location>
    --pymupdf <location>

        Set location of MuPDF, mupdfpy and PyMuPDF checkouts. 

        If location starts with `git:`, the remaining text is used in a git
        clone command, for example:

            --pymupdf 'git:--branch master https://github.com/ArtifexSoftware/PyMuPDF.git'

        Otherwise location is a directory on local machine (typically a
        checkout of PyMuPDF).

        If <location> is '' or '0', we don't use mupdfpy/pymupdf at all.

    --path <path>
        Add <path> to list of paths to test; can be specified multiple
        times. If not specified, we test with all input files.

    --perf 0|1
        If 1 we profile using `perf`.

    --pip-install 0|1
        If 0 we don't install python packages; saves a little time if venv
        already set up.

    --pymupdf-build 0|1
        If 0, do not rebuild mupdfpy or PyMuPDF. Default is 1.

    --test <testname>
        Adds to list of testnames. If not specified we use all tests.

    --timeout <timeout>
        Set fixed timeout for all tests. Otherwise we use hard-coded variable
        timeouts.
    
    --tool <toolname>
        Can be specified multiple times. Test specified tools only.
        
        To test PyMuPDF use `--tool pymupdf_mupdf_master` or `--tool
        pymupdf_mupdf_branch`.

    --venv-install 0|1

        If 0 we assume the venv is already set up; this can save a few seconds
        on startup. Otherwise (the default) we create it, upgrade pip, and
        install various PDF libraries.

'''

import json
import multiprocessing
import os
import pickle
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import time

import github


def performance(
        tests=None,
        paths=None,
        tools=None,
        timeout=None,
        internal_check=None,
        austin=None,
        cprofile=None,
        ):
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
    '''
    time_now = time.time()

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

    # Find tests.
    #
    for fnname, fn in globals().items():
        match = re.match(f'^do_([a-z]+)_([a-z0-9_]+)$', fnname)
        if match:
            testname = match.group(1)
            toolname = match.group(2)
            if toolname == 'pymupdf':
                continue
            if not tests:
                testnames.add(testname)
            if not tools:
                toolnames.add(toolname)

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
        toolversion_fn = globals().get(name)
        if not toolversion_fn:
            raise Exception(f'Need function {name}() to find version of {toolname=}.')
        
        t, e, version, ee = multiprocessing_run(toolversion_fn, timeout=30, cprofile=cprofile)
        results['toolversions'][toolname] = ee if ee else version

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
            t, e, ret = 1, 0, None
        else:
            if timeout:
                timeout2 = timeout
            elif 0 and fn.__name__ == 'do_copy_pypdf2':
                timeout2 = 600
            elif 0 and fn.__name__ == 'do_render_pdf2jpg':
                timeout2 = 600
            else:
                timeout2 = 300
            if 1:
                t, e, ret, ee = multiprocessing_run(lambda : fn(path), timeout2)
            else:
                # Don't use multiprocessing.
                log(f'### Not using multiprocessing.')
                t0 = time.perf_counter()
                ret = fn(path)
                t = time.perf_counter() - t0
                e = 0
                ee = 0
        log(f'### {i}/{num_tests}: {testname=} {path=} {toolname=} {fn.__name__=}: {t=} {ee=}')
        root = os.path.abspath(f'{__file__}/..')
        result = dict(
                testname=testname,
                path=os.path.relpath(path, root),
                toolname=toolname,
                t=t,
                e=ee,
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


def multiprocessing_run(fn, timeout, cprofile=False):
    '''
    Runs `fn()` in a separate process using Python's `multiprocessing`
    module.
    
    Returns (t, e, ret, ee):
        t: is the time in seconds to run fn().
        
        e: 0 on success, or None on timeout, or non-zero exit code from
        multiprocessing.Process, or an Exception instance raised by internal
        call of pickle.load().
        
        ret: Value returned by, or exception raised by, fn(). None if timeout
        or multiprocessing.Process invocation failed.
        
        ee: Convenience error information. A non-empty error description string
        if e is not 0 or ret is an Exception instance; otherwise 0.
    '''
    # We don't use a multiprocessing.Queue() to read version from child
    # process, because multiprocessing.Queue.get() hangs if the child
    # process fails. Instead we use a temporary file; this requires that
    # toolversions_fn() calls temp_file.flush() (or .close()) before returning,
    # otherwise we can get error from pickle.load().
    #
    with tempfile.TemporaryFile() as temp_file:
        def fn2(fn, temp_file):
            # BTW trying to get austin to profile the current process with
            # `f'austin -C -p {os.getpid()} -o out-austin2 &'` doesn't seem to
            # generate any useful data.
            if cprofile:
                import cProfile
                import pstats
                with cProfile.Profile() as pr:
                    try:
                        ret = fn()
                    except Exception as e:
                        ret = e
                ps = pstats.Stats(pr)
                ps.sort_stats('calls', 'filename')
                ps.print_stats()
            else:
                try:
                    ret = fn()
                except Exception as e:
                    ret = e
            if 0:
                # Output extra resource usage information.
                import resource
                rusage = resource.getrusage( resource.RUSAGE_SELF)
                print(f'{rusage=}')
            pickle.dump(ret, temp_file)
            temp_file.flush()
        p = multiprocessing.Process(target=fn2, args=(fn, temp_file))
        t0 = time.perf_counter()
        p.start()
        p.join(timeout)
        t = time.perf_counter() - t0
        #log(f'multiprocessing_run {fn=} {timeout=}: {p.exitcode=}')
        if p.exitcode is None:
            # Timeout.
            #log(f'Multiprocessing timeout.')
            e = None
            ret = None
            p.terminate()
            p.join(10)
            if p.exitcode is None:
                p.kill()
                p.join(10)
                if p.exitcode is None:
                    raise Exception(f'Cannot terminate multiprocess running {fn.__name__}')
        else:
            temp_file.seek(0)
            e = 0
            ret = None
            try:
                ret = pickle.load(temp_file)
            except Exception as ee:
                e = ee
            if p.exitcode:
                e = p.exitcode
        if e is None:
            ee = 'Timeout'
        elif e != 0:
            ee = f'multiprocessing.Process failure {e=}'
        elif isinstance(ret, Exception):
            ee = f'{type(ret)}: {ret}'
        else:
            ee = 0
        return t, e, ret, ee


def _import_pymupdf(install_dir):
    '''
    Imports `pymupdf` from directory `install` by temporarily modifying
    `sys.path`.
    '''
    assert isinstance(install_dir, str)
    sys.path.insert(0, install_dir)
    try:
        import pymupdf
        assert pymupdf.__file__.startswith(install_dir), f'Failed to import pymupdf from {install_dir}: {pymupdf.__file__=}'
    finally:
        del sys.path[0]


# Tool version functions.
#
# There must be one of these for each tool. Should return anything that can be
# serialised by json. Should also import anything that the tool's performance
# functions will use, to reduce startup delays when timing.
#

def get_version_pymupdf():
    # Returns a dict with version information, including detailed git
    # information about PyMuPDF and MuPDF.
    #
    import pymupdf
    #log(f'get_version_pymupdf(): {pymupdf.__file__=}')
    #log(f'get_version_pymupdf(): {pymupdf.version=}')
    #log(f'get_version_pymupdf(): {pymupdf.mupdf.Py_LIMITED_API=}')
    pymupdf_version = pymupdf.version

    pymupdf_git_sha     = getattr(pymupdf, 'pymupdf_git_sha', None)
    pymupdf_git_comment = getattr(pymupdf, 'pymupdf_git_comment', None)
    pymupdf_git_diff    = getattr(pymupdf, 'pymupdf_git_diff', None)
    pymupdf_git_branch  = getattr(pymupdf, 'pymupdf_git_branch', None)

    mupdf_git_sha       = getattr(pymupdf, 'mupdf_git_sha', None)
    mupdf_git_comment   = getattr(pymupdf, 'mupdf_git_comment', None)
    mupdf_git_diff      = getattr(pymupdf, 'mupdf_git_diff', None)
    mupdf_git_branch    = getattr(pymupdf, 'mupdf_git_branch', None)
    
    Py_LIMITED_API      = getattr(pymupdf.mupdf, 'Py_LIMITED_API', None)
    
    mupdf_version = pymupdf.mupdf_version_tuple
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
            
            Py_LIMITED_API=Py_LIMITED_API,
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
    import pymupdf
    doc = pymupdf.open(path)
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
    import pymupdf
    doc = pymupdf.open(path)
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
    import pymupdf
    doc = pymupdf.open(path)
    length = 0
    for page in doc:
        text = page.get_text()
        l = len(text)
        length += l
    print(f'{length=}')
        

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
    print(f'{os.getpid()=}: {text}')
    sys.stdout.flush()


def pymupdf_install(pymupdf_location, mupdf_location, root, local_git_dir, Py_LIMITED_API=None):
    '''
    Builds and installs PyMuPDF using pip.

    pymupdf_location:
        None, Path of PyMuPDF directory or git location of PyMuPDF, e.g.:
        git:--branch master https://github.com/ArtifexSoftware/PyMuPDF.git

    mupdf_location:
        None, Path of MuPDF directory, or gitlocation of MuPDF, e.g.:
        git:--branch master https://github.com/ArtifexSoftware/mupdf.git
    root:
        Directory into which we install pymupdf.
    local_git_dir:
        Local git directory if `pymupdf_location` starts with 'git:'.
    Py_LIMITED_API:
        If set we build for specified version of limited API.
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

    assert os.path.isdir(pymupdf_location), f'{pymupdf_location=}'

    # Build PyMuPDF.
    env = ''
    if mupdf_location:
        env = 'PYMUPDF_SETUP_MUPDF_TGZ= '
        if mupdf_location.startswith('git:'):
            env += f'PYMUPDF_SETUP_MUPDF_BUILD="{mupdf_location}" '
        else:
            env += f'PYMUPDF_SETUP_MUPDF_BUILD="{os.path.relpath(mupdf_location, pymupdf_location)}" '
    if Py_LIMITED_API:
        if Py_LIMITED_API == 'default':
            major, minor, patch = platform.python_version_tuple()
            Py_LIMITED_API = f'0x{int(major):02x}{int(minor):02x}0000'
        env += f'PYMUPDF_SETUP_Py_LIMITED_API={Py_LIMITED_API} '
    if platform.system() == 'OpenBSD':
        # Need to use system clang-python and swig because they are not
        # available in pypi.org and building from sdist fails.
        command = f'cd {pymupdf_location} && {env} python3 setup.py install'
        if root:
            command = f'{command} --root {os.path.relpath(root, pymupdf_location)}'
    else:
        command = f'cd {pymupdf_location} && {env} pip install -v .'
        if root:
            # This creates `<root>/pymupdf/{pymupdf.py,...}`.
            command = f'{command} --upgrade --target {os.path.relpath(root, pymupdf_location)}'
    log( f'Running: {command}')
    subprocess.run( command, shell=1, check=1)


if __name__ == '__main__':
    venv_install = True
    internal_check = False
    do = None
    mupdf_master_location = 'git:--branch master https://github.com/ArtifexSoftware/mupdf.git'
    mupdf_branch_location = 'git:--branch 1.24.x https://github.com/ArtifexSoftware/mupdf.git'
    pymupdf_location = 'git:--branch main https://github.com/pymupdf/PyMuPDF.git'
    mupdfpy_location = 'git:https://github.com/ArtifexSoftware/mupdfpy-julian.git'
    pymupdf_build = True
    pip_install = True
    timeout = None
    tests = []
    paths = []
    tools = []
    austin = False
    cprofile = False
    build_check = True
    perf = False

    args = iter(sys.argv[1:])
    while 1:
        try:
            arg = next(args)
        except StopIteration:
            break

        if arg == '-h' or arg == '--help':
            log(__doc__)
            sys.exit()

        elif arg == '--austin':
            austin = next(args)

        elif arg == '--build-check':
            build_check = int( next(args))

        elif arg == '--cprofile':
            cprofile = int(next(args))

        elif arg == '--internal-check':
            internal_check = int(next(args))

        elif arg == '--mupdf-branch':
            mupdf_branch_location = next(args)

        elif arg == '--mupdf-master':
            mupdf_master_location = next(args)

        elif arg == '--path':
            paths.append(next(args))
        
        elif arg == '--perf':
            perf = int( next(args))

        elif arg == '--pip-install':
            pip_install = int(next(args))

        elif arg == '--pymupdf':
            pymupdf_location = next(args)

        elif arg == '--mupdfpy':
            mupdfpy_location = next(args)

        elif arg == '--pymupdf-build':
            pymupdf_build = int(next(args))

        elif arg == '--timeout':
            timeout = float(next(args))

        elif arg == '--tool':
            name = next(args)
            assert not re.search( '[^a-zA-Z0-9_]', name), f'Tool name must contain just letters, numbers and underscores: {name!r}'
            tools.append(name)

        elif arg == '--test':
            tests.append(next(args))

        elif arg == '--venv-install':
            venv_install = int(next(args))
        else:
            raise Exception(f'Unrecognised {arg=}')

    if sys.base_prefix == sys.prefix:
        # We are not inside a venv. Re-run ourselves inside a venv so that we
        # can use pypi.org packages such as pypdf2.
        venv_name = 'pylocal'
        log(f'Re-running inside a venv.')
        command = 'true'
        # Install required system packages.
        if platform.system() == 'Linux':
            command += f' && sudo apt install poppler-utils'
        elif platform.system() == 'OpenBSD':
            command += f' && sudo pkg_add poppler-utils'
        # Create venv.
        if venv_install:
            command += f' && {sys.executable} -m venv {venv_name}'
        # Activate the venv.
        command += f' && . {venv_name}/bin/activate'
        # Install Python packages from pypi.org.
        if venv_install:
            if pip_install:
                command += f' && python -m pip install --upgrade pip'
                command += f' && python -m pip install --upgrade pypdf2 pdfminer.six pdfrw pikepdf pdf2jpg pypdfium2'
                if not pymupdf_location:
                    command += ' && python -m pip install --upgrade pymupdf'
        # Rerun ourselves inside the venv.
        command += f' &&'
        if austin:
            command += f' {austin} -C -o out-austin'
        if perf:
            command += f' perf record'
        command += f' python {shlex.join(sys.argv)}'
        log(f'Running: {command}')
        subprocess.run(command, check=True, shell=1)
        sys.exit()

    else:
        if pymupdf_location == '0':
            pymupdf_location = None
        if mupdfpy_location == '0':
            mupdfpy_location = None
        if mupdf_master_location == '0':
            mupdf_master_location = None
        if mupdf_branch_location == '0':
            mupdf_branch_location = None

        if tools:
            mupdfpy_mupdf_master = 'mupdfpy_mupdf_master' in tools
            pymupdf_mupdf_master = 'pymupdf_mupdf_master' in tools
            pymupdf_mupdf_branch = 'pymupdf_mupdf_branch' in tools
            pymupdf_mupdf_master_pla = 'pymupdf_mupdf_master_pla' in tools
        else:
            mupdfpy_mupdf_master = True
            pymupdf_mupdf_master = True
            pymupdf_mupdf_branch = True
            pymupdf_mupdf_master_pla = True
        
        def _make_pymupdf_variant_norgs(fnname, fn, install_dir):
            '''
            Make global function `{fnname}()` that imports pymupdf from
            `install_dir` and calls `fn()`.
            '''
            def fn2(install_dir=install_dir):
                _import_pymupdf(install_dir)
                return fn()
            assert getattr(globals(), fnname, None) is None
            globals()[fnname] = fn2
        
        def _make_pymupdf_variant(fnname, fn, install_dir):
            '''
            Make global function `{fnname}(path)` that imports pymupdf from
            `install_dir` and calls `fn(path)`.
            '''
            def fn2(path, install_dir=install_dir):
                _import_pymupdf(install_dir)
                return fn(path)
            assert getattr(globals(), fnname, None) is None
            globals()[fnname] = fn2
        
        def _make(name, pymupdf_location, mupdf_location, Py_LIMITED_API=None):
            '''
            Sets things up for PyMuPDF implementation called `name`, built from
            `pymupdf_location` and `mupdf_location`.
            '''
            install_dir = os.path.abspath(f'{__file__}/../install_{name}')
            log(f'Building PyMuPDF, {name=} {pymupdf_location=} {mupdf_location=} {Py_LIMITED_API=} {install_dir=}.')
            _make_pymupdf_variant_norgs(f'get_version_{name}', get_version_pymupdf, install_dir)
            _make_pymupdf_variant(f'do_copy_{name}', do_copy_pymupdf, install_dir)
            _make_pymupdf_variant(f'do_render_{name}', do_render_pymupdf, install_dir)
            _make_pymupdf_variant(f'do_text_{name}', do_text_pymupdf, install_dir)
            if pymupdf_build:
                try:
                    pymupdf_install(pymupdf_location, mupdf_location, install_dir, name, Py_LIMITED_API)
                except Exception as e:
                    if build_check:
                        raise
                    log(f'*** Ignoring exception from building {name=} {pymupdf_location=} {mupdf_location=}: {e}')
        
        if mupdfpy_mupdf_master:
            _make(
                    'mupdfpy_mupdf_master',
                    mupdfpy_location,
                    mupdf_master_location,
                    )
        if pymupdf_mupdf_master:
            _make(
                    'pymupdf_mupdf_master',
                    pymupdf_location,
                    mupdf_master_location,
                    )
        if pymupdf_mupdf_branch:
            _make(
                    'pymupdf_mupdf_branch',
                    pymupdf_location,
                    mupdf_branch_location,
                    )
        
        if pymupdf_mupdf_master_pla:
            _make(
                    'pymupdf_mupdf_master_pla',
                    pymupdf_location,
                    mupdf_master_location,
                    Py_LIMITED_API='default',
                    )
        
        performance(
                tests=tests,
                paths=paths,
                tools=tools,
                timeout=timeout,
                internal_check=internal_check,
                austin=austin,
                cprofile=cprofile,
                )
