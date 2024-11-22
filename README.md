# `llvmcpy`

`llvmcpy` automatically generates Python wrappers for the [LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html).

## Goal

The main goal of `llvmcpy` is to provide Python bindings for the LLVM project that are fast and require the lowest possible maintainance effort.
To achive this, we use CFFI to parse the (slightly adapted) LLVM-C API header files and automatically generate a set of classes and functions to interact with them in a Pythonic way.

This project is in an early stage, but allows you to run the following code:

```python
import sys
from llvmcpy import LLVMCPy

llvm = LLVMCPy()
buffer = llvm.create_memory_buffer_with_contents_of_file(sys.argv[1])
context = llvm.get_global_context()
module = context.parse_ir(buffer)
for function in module.iter_functions():
    for bb in function.iter_basic_blocks():
        for instruction in bb.iter_instructions():
            instruction.dump()
```

It's tested on all LLVM versions from 5 to 19 and on Python from 3.7 to 3.13.
Supporting newer versions of the LLVM-C API should be basically effortless.

To try it out, install LLVM and install `llvmcpy`:

```bash
sudo apt-get install llvm
python -m venv venv
source venv/bin/activate
pip install llvmcpy
```

## Naming of the generated classes/functions

The basic idea behind this project is to take the LLVM-C API, create a class for each data type and create a method for that class for each function in the API taking an argument of that data type as first argument.

This means that the following functions:

```c++
LLVMModuleRef LLVMCloneModule (LLVMModuleRef M)
```

Will become:

```python
class Module(object):
    def clone(self):
        # ...
```

Note the change in the case.
Use `help(Module.clone)` to see which LLVM-C API function a certain method is using.

Each class in `llvmcpy` is basically a wrapper around a pointer to an LLVM object.

If an API function doesn't take an LLVM object as a first argument, it will be part of the `llvm` module.

Additionally, we have some generated properties and generators for certain well known patterns in the API.

### Properties

For each function starting with `LLVMGet` or `LLVMSet` in the LLVM-C API, we generate a property. For example, consider the following two functions:

```c
void LLVMSetValueName (LLVMValueRef Val, const char *Name);
const char* LLVMGetValueName(LLVMValueRef Val);
```

In `llvmcpy` the `Get`/`Set` prefixes disappear, along with `Value` (the name of the class) and you can use them as properties of the `Value` class, e.g.:

```python
my_value.name = "sum"
print(my_value.name)
```

### Generators

The LLVM-C API has a recurrent pattern which allows you to navigate through the hierarchy of its class hierarchy, i.e. the pair of `LLVMGetSomething` and `LLVMGetNextSomething` functions.
`Something` can be `Function`, `BasicBlock` and so on. `llvmcpy` identifies these patterns and produces a generator method which allows you to iterate over these objects in a Pythonic way:

```python
for function in module.iter_functions():
    for bb in function.iter_basic_blocks():
        for instruction in bb.iter_instructions():
            # ...
```

## Where are my bindings?

Bindings are automatically generated in a lazy way.
Multiple installations of LLVM are supported, just set the `LLVM_CONFIG` environment variable to the `llvm-config` program in the `bin/` directory of your LLVM installation and everything should work fine.

The bindings are generated in a Python script which is stored in `$XDG_CACHE_DIR/llvmcpy/` (typically `~/.cache/llvmcpy`) in a directory whose name is obtained by hashing the full path of the output of `llvm-config --prefix` concatenated with the LLVM version number.
For example, for LLVM 19 installed in `/usr` you'll find the API bindings in `~/.cache/llvmcpy/7fea08f2e9d5108688f692e686c8528b914eda563e7069b25ef18c49ba96d7f2-19`.

To generate the bindings, a working C preprocessor must be available in the system. By default, `cpp` (the C preprocessor part of GCC) is used. If it's not available we check if `clang` is available in the LLVM installation and use it.

## License and credits

This project is developed and maintained by rev.ng Labs as part of the rev.ng project, and it's released under the MIT license.
