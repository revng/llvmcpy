[![Build Status](https://travis-ci.org/revng/llvmcpy.svg?branch=master)](https://travis-ci.org/revng/llvmcpy)

# Python bindings for the LLVM C-API

## Goal

The main goal of llvmcpy is to provide Python bindings for the LLVM
project that are fast and require the lowest possible maintainance
effort. To achieve this, we use CFFI to parse the (slightly adapted)
header files for the [LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html) and
automatically generate a set of classes and functions to interact with
them in a Pythonic way.

This project is in an early stage, but allows you to run the following
code:

    import sys
    from llvmcpy.llvm import *

    buffer = create_memory_buffer_with_contents_of_file(sys.argv[1])
    context = get_global_context()
    module = context.parse_ir(buffer)
    for function in module.iter_functions():
        for bb in function.iter_basic_blocks():
            for instruction in bb.iter_instructions():
                instruction.dump()

It has been tested with LLVM 3.4, 3.8 and 3.9. Supporting older and
newer versions of the [LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html) should be
basically effortless.

## Prerequisites and Installation

The library relies on there being an LLVM installation (built with either `LLVM_BUILD_LLVM_DYLIB` or `BUILD_SHARED_LIBS` enabled).
It also requires a working C preprocessor to generate the language bindings. By default cpp (the C preprocessor part of GCC) is used. If
it\'s not available we check if clang is available in the LLVM installation and use it.

To try it out install LLVM, either by using a packet manager like apt or homebrew or bulding from source.
Then get llvmcpy using `pip`:

    pip install llvmcpy

## Developing 

If you want to develop or use the development version:

```
git clone https://github.com/revng/llvmcpy.git
cd llvmcpy
```

Now you need to install llvmcpy. You can either install
globally on your system in develop mode:

```
sudo pip install -e .
```

or install llvmcpy into a virtual python environment
in develop mode to avoid installing globally:

```
virtualenv venv
source venv/bin/activate
pip install -e .
```


## Naming of the generated classes/functions

The basic idea behind this project is to take the [LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html) function,
create a class for each data type and create a method for that class for
each function in the API taking an argument of that data type as first
argument.

This means that the following functions:

    LLVMModuleRef LLVMCloneModule (LLVMModuleRef M)

Will become:

    class Module(object):
        def clone(self):
            # ...

Note the change in the case. Use `help(Module.clone)` to see which
[LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html)
function a certain method is using.

Each class in llvmcpy is basically a wrapper around a pointer to an LLVM
object.

If an API function doesn\'t take an LLVM object as a first argument, it
will be part of the llvm module. Moreover, we also have some also
generated properties and generators for certain well known patterns in
the API.

### Properties

For each function starting with LLVMGet or LLVMSet in the [LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html), we   generate a property. For example, consider the following two
functions:

        void LLVMSetValueName (LLVMValueRef Val, const char *Name)
        const char* LLVMGetValueName(LLVMValueRef Val)

In llvmcpy the Get/Set prefixes disappear, along with Value (the
name of the class) and you can use them as properties of the Value
class, e.g.:

        my_value.name = "sum"
        print my_value.name

### Generators

The [LLVM-C API](http://llvm.org/docs/doxygen/html/group__LLVMC.html) has a
recurrent pattern which allows you to navigate through the hierarchy
of its class hierarchy, i.e. the pair of LLVMGetSomething and
LLVMGetNextSomething functions. Something can be Function,
BasicBlock and so on. llvmcpy identifies these patterns and produces
a generator method which allows you to iterate over these objects in
a Pythonic way:

        for function in module.iter_functions():
            for bb in function.iter_basic_blocks():
                for instruction in bb.iter_instructions():
                    # ...

## Where are my bindings?

Bindings are automatically generated in a lazy way. Multiple
installations of LLVM are supported, just set the LLVM\_CONFIG
environment variable to the llvm-config program in the bin/ directory of
your LLVM installation and everything should work fine.

The bindings are generated in a Python script which is stored in
`$XDG_CACHE_DIR/llvmcpy/` (typically `~/.cache/`) in a directory whose
name is obtained by hashing the full path of the output of
`llvm-config --prefix` concatenated with the LLVM version number. For
example, for LLVM 3.9.0 installed in `/usr` you\'ll find the API bindings
in
```
~/.cache/llvmcpy/7fea08f2e9d5108688f692e686c8528b914eda563e7069b25ef18c49ba96d7f2-3.9.0```
```
or on OS X using LLVM 6.0.0 

```
~/Library/Caches/llvmcpy/73081091cb6f547a3bddb5a6099fbc1d770431225c5bc0bb69ab0420ced3160a-6.0.0/
```


## License and credits

This project is developed and maintained by Alessandro Di Federico
(<ale+llvmcpy@clearmind.me>) as a part of the [rev.ng](https://rev.ng/)
project, and it\'s released under the MIT license.
