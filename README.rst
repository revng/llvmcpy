****
Goal
****

The main goal of `llvmcpy` is to provide Python bindings for the LLVM project
that are fast and require the lowest possible maintainance effort. To achive
this, we use CFFI to parse the (slightly adapted) header files for the `LLVM-C
API`_ and automatically generate a set of classes and functions to interact with
them in a Pythonic way.

This project is in an early stage, but allows you to run the following code::

    import sys
    from llvmcpy.llvm import *

    buffer = create_memory_buffer_with_contents_of_file(sys.argv[1])
    context = get_global_context()
    module = context.parse_ir(buffer)
    for function in module.iter_functions():
        for bb in function.iter_basic_blocks():
            for instruction in bb.iter_instructions():
                instruction.dump()

It has been tested with LLVM 3.4, 3.8 and 3.9. Supporting older and newer
versions of the `LLVM-C API`_ should be basically effortless.

To try it out install LLVM and get `llvmcpy` using `pip`::

    sudo apt-get install llvm
    pip install llvmcpy

*****************************************
Naming of the generated classes/functions
*****************************************

The basic idea behind this project is to take the `LLVM-C API`_ function, create
a class for each data type and create a method for that class for each function
in the API taking an argument of that data type as first argument.

This means that the following functions::

    LLVMModuleRef LLVMCloneModule (LLVMModuleRef M)

Will become::

    class Module(object):
        def clone(self):
            # ...

Note the change in the case. Use ``help(Module.clone)`` to see which `LLVM-C
API`_ function a certain method is using.

Each class in `llvmcpy` is basically a wrapper around a pointer to an LLVM
object.

If an API function doesn't take an LLVM object as a first argument, it will be
part of the `llvm` module. Moreover, we also have some also generated properties
and generators for certain well known patterns in the API.

:Properties: For each function starting with `LLVMGet` or `LLVMSet` in the
             `LLVM-C API`_, we generate a property. For example, consider the
             following two functions::

               void LLVMSetValueName (LLVMValueRef Val, const char *Name)
               const char* LLVMGetValueName(LLVMValueRef Val)

             In `llvmcpy` the `Get`/`Set` prefixes disappear, along with `Value`
             (the name of the class) and you can use them as properties of the
             `Value` class, e.g.::

               my_value.name = "sum"
               print my_value.name

:Generators: The `LLVM-C API`_ has a recurrent pattern which allows you to
             navigate through the hierarchy of its class hierarchy, i.e. the
             pair of `LLVMGetSomething` and `LLVMGetNextSomething`
             functions. `Something` can be `Function`, `BasicBlock` and so
             on. `llvmcpy` identifies these patterns and produces a generator
             method which allows you to iterate over these objects in a Pythonic
             way::

               for function in module.iter_functions():
                   for bb in function.iter_basic_blocks():
                       for instruction in bb.iter_instructions():
                           # ...

**********************
Where are my bindings?
**********************

Bindings are automatically generated in a lazy way. Multiple installations of
LLVM are supported, just set the `LLVM_CONFIG` environment variable to the
`llvm-config` program in the `bin/` directory of your LLVM installation and
everything should work fine.

The bindings are generated in a Python script which is stored in
``$XDG_CACHE_DIR/llvmcpy/`` (typically ``~/.cache/``) in a directory whose name
is obtained by hashing the full path of the output of ``llvm-config --prefix``
concatenated with the LLVM version number. For example, for LLVM 3.9.0 installed
in `/usr` you'll find the API bindings in
``~/.cache/llvmcpy/7fea08f2e9d5108688f692e686c8528b914eda563e7069b25ef18c49ba96d7f2-3.9.0``.

To generate the bindings a working C preprocessor must be available in the
system. By default `cpp` (the C preprocessor part of GCC) is used. If it's not
available we check if `clang` is available in the LLVM installation and use it.

*******************
License and credits
*******************

This project is developed and maintained by Alessandro Di Federico
(ale+llvmcpy@clearmind.me) as a part of the `rev.ng`_ project, and it's released
under the MIT license.

.. _rev.ng: https://rev.ng/
.. _LLVM-C API: http://llvm.org/docs/doxygen/html/group__LLVMC.html
