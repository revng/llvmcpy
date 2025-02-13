import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import List, Optional

import platformdirs

from ._generator import Generator


def _get_version() -> str:
    if sys.version_info < (3, 8):
        import pkg_resources

        return pkg_resources.get_distribution(__name__).version
    else:
        from importlib.metadata import version

        return version(__name__)


class LLVMCPy:
    def __init__(self, llvm_config: Optional[str] = None):
        self._search_paths = os.environ.get("PATH", os.defpath).split(os.pathsep)
        if llvm_config is not None:
            self._llvm_config = llvm_config
        else:
            self._llvm_config = self._find_program("LLVM_CONFIG", ["llvm-config"])
        self._search_paths.insert(0, self._run_llvm_config(["--bindir"]))
        self.version = self._run_llvm_config(["--version"])
        self.major_version = self.version.split(".")[0]

        module = self._get_module()
        for elem in dir(module):
            setattr(self, elem, getattr(module, elem))

    def _get_module(self):
        hash_obj = hashlib.sha256()
        hash_obj.update(self._llvm_config.encode("utf-8"))
        hash_obj.update(b"\x00" + _get_version().encode("utf-8"))

        dir_name = hash_obj.hexdigest() + "-" + self.version
        cache_dir = Path(platformdirs.user_cache_dir("llvmcpy")) / dir_name
        llvmcpyimpl_py = cache_dir / "llvmcpyimpl.py"
        if not llvmcpyimpl_py.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._make_wrapper(cache_dir, llvmcpyimpl_py)

        # These 3 lines are equivalent to importing `llvmcpyimpl_py` file but they:
        # * Do not require modifying sys.path
        # * Do not pollute the `sys.modules` dictionary, allowing for multiple
        #   versions of llvmcpy to be loaded at the same time
        spec = importlib.util.spec_from_file_location(f"llvmcpy-{dir_name}", llvmcpyimpl_py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _get_libraries(self, cache_dir: Path) -> List[Path]:
        if sys.platform == "win32":
            extension = ".dll"
        elif sys.platform == "darwin":
            extension = ".dylib"
        else:
            extension = ".so"

        # If the cache_dir has libLLVM.<ext>, short-circuit and return
        cached_library = cache_dir / f"libLLVM{extension}"
        if cached_library.is_file():
            return [cached_library]

        libraries = []
        libdir_path = Path(self._run_llvm_config(["--libdir"]))
        shared_mode = self._run_llvm_config(["--shared-mode"])
        if shared_mode == "shared":
            # The names returned by `libnames` are `.so`s that can be used
            for libname in self._run_llvm_config(["--libnames"]).split(" "):
                lib_path = libdir_path / libname
                if extension in lib_path.suffixes:
                    libraries.append(lib_path)
            return libraries

        # Sometimes `$(llvm-config --shared-mode) != shared`, but the shared
        # library is still installed on the system. The following are some
        # heuristics that hopefully find a good candidate

        # Check for libLLVM-<major>.<ext>
        path = libdir_path / f"libLLVM-{self.major_version}{extension}"
        if path.is_file():
            return [path]

        # Check for libLLVM.<ext>
        path = libdir_path / f"libLLVM{extension}"
        if path.is_file():
            return [path]

        # use glob on `libLLVM*.<ext>*`
        for lib_path in libdir_path.glob(f"libLLVM*{extension}*"):
            if lib_path.is_file() and not lib_path.is_symlink():
                libraries.append(lib_path)

        if len(libraries) > 0:
            return libraries

        for candidate_executable in ("clang", "gcc", "clang++", "g++", "cc"):
            executable = self._which(candidate_executable)
            if executable is not None:
                break

        # It's possible to make a shared library from the static one(s). This
        # might use a lot of resources, so ask the user to do it themselves
        # instead of doing it ourselves.
        # TODO: make versions for Mac and Windows as well
        if shared_mode == "static" and executable is not None and sys.platform == "linux":
            config_path = str(Path(self._llvm_config).resolve())
            cache_lib_path = str(cached_library.resolve())
            sys.stderr.write("LLVM has been compiled in static mode while this program requires\n")
            sys.stderr.write("a dynamic library. To make one, run the following command:\n")
            sys.stderr.write(
                f"{executable} -shared -o {cache_lib_path} $({config_path} --ldflags) "
                # --whole-arhive needed otherwise the linker would discard the unused symbols
                + f"-Wl,--whole-archive $({config_path} --libs) -Wl,--no-whole-archive "
                + f"$({config_path} --system-libs)\n"
            )
            sys.stderr.flush()

        raise ValueError("No valid LLVM libraries found, LLVM must be built with BUILD_SHARED_LIBS")

    def _make_wrapper(self, cache_dir: Path, wrapper_file: Path):
        cpp = self._find_program("CPP", ["clang", "cpp", "gcc", "cc"])
        libraries = self._get_libraries(cache_dir)
        include_dir = Path(self._run_llvm_config(["--includedir"]))
        generator = Generator(cpp, libraries, include_dir)
        generator.generate_wrapper(wrapper_file)

    def _run_llvm_config(self, args: List[str]) -> str:
        """Invoke llvm-config with the specified arguments and return the output"""
        assert self._llvm_config is not None
        return subprocess.check_output([self._llvm_config, *args]).decode("utf-8").strip()

    def _find_program(self, env_variable: str, names: List[str]) -> str:
        """Find an executable in the env_variable environment variable or in PATH in
        with one of the names in the argument names."""

        for name in [os.environ.get(env_variable, ""), *names]:
            path = self._which(name)
            if path is not None:
                return path

        raise RuntimeError(
            f"Couldn't find {env_variable} or any of the following executables in PATH: "
            + " ".join(names)
        )

    def _which(self, name: str) -> Optional[str]:
        return which(name, path=os.pathsep.join(self._search_paths))
