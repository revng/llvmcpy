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


class LLVMCPy:
    def __init__(self, llvm_config: Optional[str] = None):
        self._search_paths = os.environ.get("PATH", os.defpath).split(os.pathsep)
        if llvm_config is not None:
            self._llvm_config = llvm_config
        else:
            self._llvm_config = self._find_program("LLVM_CONFIG", ["llvm-config"])
        self._search_paths.insert(0, self._run_llvm_config(["--bindir"]))
        self.version = self._run_llvm_config(["--version"])

        module = self._get_module()
        for elem in dir(module):
            setattr(self, elem, getattr(module, elem))

    def _get_module(self):
        path_hash = hashlib.sha256(self._llvm_config.encode("utf-8")).hexdigest()
        dir_name = path_hash + "-" + self.version
        cache_dir = Path(platformdirs.user_cache_dir("llvmcpy")) / dir_name
        llvmcpyimpl_py = cache_dir / "llvmcpyimpl.py"
        if not llvmcpyimpl_py.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._make_wrapper(llvmcpyimpl_py)

        # These 3 lines are equivalent to importing `llvmcpyimpl_py` file but they:
        # * Do not require modifying sys.path
        # * Do not pollute the `sys.modules` dictionary, allowing for multiple
        #   versions of llvmcpy to be loaded at the same time
        spec = importlib.util.spec_from_file_location(f"llvmcpy-{dir_name}", llvmcpyimpl_py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _make_wrapper(self, path: Path):
        cpp = self._find_program("CPP", ["clang", "cpp", "gcc", "cc"])

        if sys.platform == "win32":
            extension = ".dll"
        elif sys.platform == "darwin":
            extension = ".dylib"
        else:
            extension = ".so*"
        libraries = list(Path(self._run_llvm_config(["--libdir"])).glob(f"libLLVM*{extension}"))
        include_dir = Path(self._run_llvm_config(["--includedir"]))
        generator = Generator(cpp, libraries, include_dir)
        generator.generate_wrapper(path)

    def _run_llvm_config(self, args: List[str]) -> str:
        """Invoke llvm-config with the specified arguments and return the output"""
        assert self._llvm_config is not None
        return subprocess.check_output([self._llvm_config, *args]).decode("utf-8").strip()

    def _find_program(self, env_variable: str, names: List[str]) -> str:
        """Find an executable in the env_variable environment variable or in PATH in
        with one of the names in the argument names."""

        for name in [os.environ.get(env_variable, ""), *names]:
            path = which(name, path=os.pathsep.join(self._search_paths))
            if path is not None:
                return path

        raise RuntimeError(
            f"Couldn't find {env_variable} or any of the following executables in PATH: "
            + " ".join(names)
        )
