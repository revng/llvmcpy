#!/usr/bin/env python

import sys
import keyword
import os
import subprocess
import tempfile
import shutil
import fnmatch
import re
import hashlib
import appdirs
import cffi
import pycparser
import pycparser.c_generator
from cffi import FFI
from glob import glob
from itertools import chain
from collections import defaultdict
# For Python version <3.3, shutil.which is provided by the shutilwhich module.
try:
    from shutil import which
except ImportError:
    from shutilwhich import which

def run_llvm_config(args):
    """Invoke llvm-config with the specified arguments and return the output"""

    global llvm_config
    return subprocess.check_output([llvm_config] + args).decode("utf-8").strip()

def find_program(env_variable, names):
    """Find an executable in the env_variable environment variable or in PATH in
    with one of the names in the argument names."""

    global search_paths
    for name in chain([os.environ.get(env_variable, "")], names):
        path = which(name, path=search_paths)
        if path:
            return path

    raise RuntimeError("Couldn't find "
                       + env_variable
                       + " or any of the following executables in PATH: "
                       + " ".join(names))

def is_llvm_type(name):
    return name.startswith("struct LLVM") or name.startswith("LLVM")

def remove_llvm_prefix(name):
    assert is_llvm_type(name)
    if name.startswith("struct "):
        name = name[len("struct "):]
    name = name[len("LLVM"):]
    if name.startswith("Opaque"):
        name = name[len("Opaque"):]
    return name

def to_python_case(name):
    """Convert "GetLLVMFunctionID" to a more pythonic "get_llvm_function_id" """

    # If it's all upper cases, simply return the lower case version
    if name.isupper():
        return name.lower()

    result = ""
    # When the case in the input changes, emit a _
    for prev, cur, next in zip("a" + name[:-1], name, name[1:] + "a"):
        if ((prev.islower() and cur.isupper())
            or (next.islower() and cur.isupper())):
            result += "_"
        result += cur.lower()

    # If the string ends with capital letters, remove the last _
    if name[-2:].isupper():
        result = result[:-2] + result[-1:]

    # Discard the initial _
    return result[1:]

def normalize_name(original_class_name, original_name):
    """Normalizes the case and remove the name of the class from the method name
    in several common cases (e.g., Value.get_value_name => Value.get_name)"""

    # BasicBlockAsValue -> GetBasicBlockAsValue
    if (original_class_name is not None
        and original_name.startswith(original_class_name)):

        to_skip = len(original_class_name)
        return normalize_name(original_class_name, original_name[to_skip:])

    name = to_python_case(original_name)
    class_name = "" if original_class_name is None \
                 else to_python_case(original_class_name)

    prefix_get = "get_" + class_name + "_"
    prefix_set = "set_" + class_name + "_"
    if name.startswith(prefix_get):
        # Remove Value from GetValueName
        name = "get_" + name[len(prefix_get):]
    elif name.startswith(prefix_set):
        # Remove Value from SetValueName
        name = "set_" + name[len(prefix_set):]
    elif class_name and name.endswith("_in_" + class_name):
        # Remove InContext from ParseIrInContext
        name = name[:-len(class_name) - 4]
    elif class_name and name.endswith("_" + class_name):
        # Remove Value from DumpValue
        name = name[:-len(class_name) - 1]

    return name

def create_function(library, name, prototype,
                    class_name=None, properties=None, classes=None):
    """Return a string containing one or more Python functions wrapping in a
    more Pythonic way the specified library function"""

    result = ""
    is_class_method = class_name is not None
    skip_args = 1 if is_class_method else 0

    # Create a list to collect the string representation of the arguments to to
    # the library function
    arguments = []

    # Create a list to collect all the pointers to pointers to LLVM objects,
    # which represent an out argument
    out_args = []

    # Create a list to collect all the char ** arguments, which are usually
    # employed to return textual error messages
    out_strings = []

    effective_arguments = prototype.args[skip_args:]
    for index, arg_type in enumerate(effective_arguments):
        if arg_type.kind == "pointer":
            pointee = arg_type.item
            if (pointee.kind == "pointer" and pointee.item.kind == "struct"
                and is_llvm_type(pointee.item.cname)):
                # LLVM object **: the function is returning a reference to an
                # object or it's an array
                arguments.append(("([x.in_ptr() for x in arg{}] "
                                  + "if type(arg{}) is list "
                                  + "else arg{}.out_ptr())").format(index,
                                                                    index,
                                                                    index))
                out_args.append(index)
            elif (pointee.kind == "pointer" and pointee.item.kind == "primitive"
                  and pointee.item.cname == "char"):
                # char **: the function is returning a string
                arguments.append("arg{}".format(index))
                out_strings.append(index)
            elif (pointee.kind == "struct"
                  and is_llvm_type(pointee.cname)):
                # LLVM object *: the function takes is taking an LLVM object
                arguments.append("arg{}.in_ptr()".format(index))
            elif pointee.kind == "primitive" and pointee.cname == "char":
                # char *: TODO
                arguments.append("""arg{}.encode("utf-8")""".format(index))
            elif pointee.kind == "primitive":
                # int *: TODO
                arguments.append("arg{}".format(index))
            elif pointee.kind == "void":
                # void *: TODO
                arguments.append("arg{}".format(index))
            else:
                print(pointee)
                assert False
        elif arg_type.kind == "primitive" or arg_type.kind == "enum":
            # Enumeration
            arguments.append("arg{}".format(index))
        elif arg_type.kind == "function":
            # Function pointer
            arguments.append("arg{}".format(index))
        else:
            print(prototype)
            assert False

    # Build arguments for the function call
    function_arguments = ["arg" + str(x)
                          for x in range(len(effective_arguments))]

    # Compute pythonic name
    method_name = normalize_name(class_name, name[4:])

    # If the method starts with get_ or set_ also create the appropriate
    # property
    if is_class_method:
        if method_name.startswith("get_") and len(function_arguments) == 0:
            properties[method_name[4:]] = ((name, method_name),
                                           properties[method_name[4:]][1])
        elif method_name.startswith("set_") and len(function_arguments) == 1:
            properties[method_name[4:]] = (properties[method_name[4:]][0],
                                           (name, method_name))

    # Function to compute header of the generated function
    def header():
        zeroth_argument = ["self"] if is_class_method else []
        # Discard None arguments, they have been removed
        args = filter(lambda x: x is not None, function_arguments)
        function_arguments_str = ", ".join(zeroth_argument + list(args))
        return ("""
    def {}({}):
        """ + "\"\"\"See {}\"\"\"\n").format(method_name,
                                             function_arguments_str,
                                             name)

    # Function to compute the function call
    def call():
        zeroth_argument = ["self.in_ptr()"] if is_class_method else []
        arguments_str = ", ".join(zeroth_argument + arguments)
        return "{}.{}({})".format(library, name, arguments_str)

    # Prepare creation of the function body handling special cases
    return_type = prototype.result
    last_arg = effective_arguments[-1] if effective_arguments else None

    # Look for pairs of pointer to LLVM objects followed by an integer: they
    # often represent pointer-to-first-element + length pairs describing an
    # array of LLVM objects.
    for out_arg in out_args:
        # If it's not the last argument and the next is an integer
        if ((len(effective_arguments) > out_arg + 1)
            and (effective_arguments[out_arg + 1].kind == "primitive")
            and (effective_arguments[out_arg + 1].cname == "unsigned int")):

            # Replace the argument with the lenght of the previous argument
            arguments[out_arg + 1] = "len(arg{})".format(out_arg)

            # Remove the function argument, we know how to compute it
            function_arguments[out_arg + 1] = None


    # Special case: the function returns a boolean and has either a single LLVM
    # object out argument or an out string argument. In this case we will remove
    # the out argument (if present), create a temporary object and return in
    # it. More over in case of error we'll throw an exception, possibly with an
    # appropriate error message.
    has_out_arg = len(out_args) == 1
    has_error_message = ((len(out_strings) == 1)
                         and (out_strings[0] == len(arguments) - 1))

    if (return_type.kind == "primitive"
        and return_type.cname == "int"
        and (has_out_arg or has_error_message)):

        # Has an out LLVM object, we will create a temporary object, pass it to
        # the function and then return it
        if has_out_arg:
            # Get the index of the out argument
            out_arg = out_args[0]

            # Replace the out argument with a temporary object we're going to
            # return
            arguments[out_arg] = "result.out_ptr()"

            # Remove the out argument from the function prototype
            function_arguments[out_arg] = None

        # There's an out string argument: it's the error message
        if has_error_message:
            # Take its index
            str_arg = out_strings[0]

            # Replace the out string argument with a temporary char **
            arguments[str_arg] = "error_str"

            # Remove the argument from the function prototype
            function_arguments[str_arg] = None

        # Print the function header
        result += header()

        # If we have an error message create a temporary char ** and use it as
        # the exception error message, otherwise just use the "Error" string
        error_message = "\"Error\""
        if has_error_message:
            result += """        error_str = ffi.new("char **")""" + "\n"
            error_message = "ffi.string(error_str[0])"

        result_type = "None"
        if has_out_arg:
            # Print the function body: first create a temporary object we will
            # return, then call the function replacing the out argument with
            # that object, take the boolean result and if there's an error throw
            # an exception
            result_type = prototype.args[out_arg + skip_args].item.item.cname
            result_type = remove_llvm_prefix(result_type)
            result_type += "()"

        result += """        result = {}
        failure = {}
        if failure != 0:
            raise LLVMException({})
        return result""".format(result_type, call(), error_message)

    elif (return_type.kind == "pointer"
          and return_type.item.kind == "primitive"
          and return_type.item.cname == "char"
          and last_arg and last_arg.kind == "pointer"
          and last_arg.item.kind == "primitive"
          and last_arg.item.cname in unsigned_ints):

        # Special case: the function returns a string and
        # the last argument is a pointer to an unsigned int/long:
        # This often represents returning a string (that may contain
        # NUL characters) with a length
        function_arguments[-1] = 'encoding="utf-8"'
        arguments[-1] = "length_buffer"

        # Print function header
        result += header()

        # Print the function body: first create a buffer for the length, then
        # call the function and build the string return value from the
        # returned char pointer and the length
        result += """        length_buffer = ffi.new("{}[1]")
        ptr = {}
        length = length_buffer[0]
        raw_bytes = ffi.unpack(ptr, length)
        return raw_bytes.decode(encoding) if encoding else raw_bytes
        """.format(last_arg.item.cname, call())

    else:
        # Regular case

        # Print function header
        result += header()

        # Handle common return types
        if return_type.kind == "pointer":
            pointee = return_type.item

            # Are we returning an LLVM object? Wrap it in the appropriate class
            if (pointee.kind == "struct"
                and is_llvm_type(pointee.cname)):

                return_type_name = remove_llvm_prefix(pointee.cname)
                result += "        return {}({})".format(return_type_name,
                                                         call())

            elif pointee.kind == "primitive" and pointee.cname == "char":
                # Returning a char **, wrap it as a Python string
                result += "        return ffi.string({})".format(call())
            else:
                # All the rest
                result += "        return " + call()
        else:
            # All the rest
            result += "        return " + call()

    # Generate pythonic way to iterate over list of objects (e.g., functions in
    # a module)
    #
    # We need: LLVMGetFirstSomething, with a single argument (self), returning a
    # pointer to an LLVM object, that has a corresponding LLVMGetNextSomething
    # which takes a Something object and returns a Something object
    if (is_class_method
        and name.startswith("LLVMGetFirst")
        and len(prototype.args) == 1
        and return_type.kind == "pointer"
        and return_type.item.kind == "struct"
        and is_llvm_type(return_type.item.cname)):

        full_iterated_type_name = return_type.item.cname
        iterated_type_name = remove_llvm_prefix(full_iterated_type_name)
        iterated_name = name[len("LLVMGetFirst"):]

        # Check if we have a Somthing class
        if full_iterated_type_name in classes:
            # Look for the LLVMGetNextSomething method
            for library, name, prototype in classes[full_iterated_type_name]:
                # Check if the prototype is what we expect
                if (name == "LLVMGetNext" + iterated_name
                    and len(prototype.args) == 1
                    and prototype.args[0] == return_type
                    and prototype.result == return_type):

                    # OK, we can emit the generator functiono to iterate over
                    # Something
                    docstring = "\"\"\"See LLVMGetFirst{} and {}\"\"\""
                    docstring = docstring.format(iterated_name, name)
                    result += """

    def iter_{}s(self):
        {}
        next = self.{}()
        while next is not None:
            yield next
            next = next.{}()""".format(normalize_name(class_name,
                                                      iterated_name),
                                       docstring,
                                       method_name,
                                       normalize_name(iterated_type_name,
                                                      name[4:]))
    return result

header_blacklist = ["llvm/Support/DataTypes.h",
                    "llvm-c/DataTypes.h",
                    "math.h",
                    "stddef.h",
                    "cstddef",
                    "sys/types.h",
                    "stdbool.h"]
def clean_include_file(in_path):
    """Clean the LLVM-C API headers files for parsing by CFFI: remove standard
    includes and static inline functions"""
    out_path = in_path + ".filtered"

    with open(in_path, "r") as in_file, open(out_path, "w") as out_file:
        skip_block = False
        for line in in_file:
            skip = False
            for header in header_blacklist:
                if line.startswith("#include ") and header in line:
                    skip = True

            if line.startswith("static inline"):
                skip_block = True

            if skip or skip_block:
                out_file.write("// ")

            line = re.sub(r"\b0U\b", "0", line)
            out_file.write(line)

            if line.startswith("}"):
                skip_block = False
    shutil.move(out_path, in_path)

def get_libraries():
    extension = None
    if sys.platform == 'win32':
        extension = '.dll'
    elif sys.platform == 'darwin':
        extension = '.dylib'
    else:
        extension = '.so'
    pattern = "libLLVM*{0}".format(extension)
    return glob(os.path.join(run_llvm_config(["--libdir"]), pattern))


def parse_headers():
    """Parse the header files of the LLVM-C API and produce a list of libraries
    and the CFFI cached data"""

    # Identify the C preprocessor
    # TODO: this is the only non-portable part of the code
    cpp = find_program("CPP", ["clang", "cpp", "gcc", "cc"])

    # Take the list of LLVM libraries
    lib_files = get_libraries()

    # Take the LLVM include path
    llvm_include_dir = run_llvm_config(["--includedir"]).strip()

    # Create a temporary directory in which we will copy the headers and adapt
    # them a little for CFFI parsing
    temp_directory = tempfile.mkdtemp()
    try:
        os.mkdir(os.path.join(temp_directory, "llvm"))
        llvm_c_dir = os.path.join(temp_directory, "llvm-c")
        shutil.copytree(os.path.join(llvm_include_dir, "llvm-c"), llvm_c_dir)
        shutil.copytree(os.path.join(llvm_include_dir, "llvm", "Config"),
                        os.path.join(temp_directory, "llvm", "Config"))

        # Find and adapt all the header files
        include_files = []
        skip = len(temp_directory) + 1
        for root, dirnames, filenames in os.walk(llvm_c_dir):
            for filename in fnmatch.filter(filenames, '*.h'):
                if filename != "DataTypes.h":
                    header_path = os.path.join(root, filename)
                    include_files.append(header_path[skip:])
                    clean_include_file(header_path)

        # Create all.c, a C file including all the headers
        all_c_path = os.path.join(temp_directory, "all.c")
        all_includes = "#include \""
        all_includes += "\"\n#include \"".join(include_files) + "\""
        with open(all_c_path, "w") as all_c:
            all_c.write("typedef long unsigned int size_t;\n")
            all_c.write("typedef int off_t;\n")
            all_c.write(all_includes + "\n")

        # Preprocess all.c
        all_c_preprocessed = os.path.join(temp_directory, "all.prep.c")
        subprocess.check_call([cpp,
                               "-U__GNUC__",
                               "-I" + temp_directory,
                               "-I" + llvm_include_dir,
                               "-E",
                               "-o" + all_c_preprocessed,
                               all_c_path])

        # Parse enum definitions
        enums = handle_enums(all_c_preprocessed)

        # Let CFFI parse the preprocessed header
        with open(all_c_preprocessed) as c_file:
            ffi.cdef(c_file.read(), override=True)

        # Compile the CFFI data and save them so we can return it
        ffi.set_source("ffi", None)
        ffi.compile(temp_directory)
        ffi_code = open(os.path.join(temp_directory, "ffi.py"), "r").read()

    finally:
        # Cleanup
        shutil.rmtree(temp_directory)

    # Create a list of the LLVM libraries and dlopen them
    def basename(x):
        result = os.path.basename(x)
        result = os.path.splitext(result)[0]
        result = result.replace(".", "")
        result = result.replace("-", "")
        return result

    libs = zip(lib_files,
               map(basename, lib_files),
               map(ffi.dlopen, lib_files))

    return list(libs), ffi_code, enums

def handle_enums(all_c_preprocessed):
    """
    Parse enum typedefs and return a dictionary mapping from typedefs to values
    as well as from values to the integer representation of the enum.

    Returns a dict with the following structure (example):
    {
        "Opcode": {1: 'Ret', 2: 'Br', ..., 'Ret': 1, 'Br': 2, ...},
        "Visibility": {0: 'DefaultVisibility', ..., 'DefaultVisibility': 0, ...},
        ...
    }

    """
    def remove_prefix(name):
        if name.startswith("LLVM") and not name.startswith("LLVM_"):
            return name[4:]

    def handle_expression(variables, expression):
        expression_type = type(expression)
        if expression_type is pycparser.c_ast.Constant:
            return int(expression.value, 0)
        elif expression_type is pycparser.c_ast.ID:
            assert expression.name in variables
            return variables[expression.name]
        elif expression_type is pycparser.c_ast.BinaryOp:
            left = handle_expression(variables, expression.left)
            right = handle_expression(variables, expression.right)
            if expression.op == "|":
                return left | right
            elif expression.op == "&":
                return left & right
            elif expression.op == "+":
                return left + right
            elif expression.op == "-":
                return left - right
            elif expression.op == "<<":
                return left << right
            elif expression.op == ">>":
                return left >> right
            else:
                assert False
        else:
            assert False

    class EnumVisitor(pycparser.c_ast.NodeVisitor):
        def __init__(self):
            self._name = None
            self.enums = {}

        def visit_Typedef(self, typedef):
            self._name = remove_prefix(typedef.name)
            self.generic_visit(typedef)

        def visit_EnumeratorList(self, enum_list):
            values = {}
            last = -1
            for enumerator in enum_list.children():
                enumerator = enumerator[1]
                assert type(enumerator) is pycparser.c_ast.Enumerator
                value = enumerator.value
                if value is None:
                    values[enumerator.name] = last + 1
                else:
                    values[enumerator.name] = handle_expression(values, value)

                last = values[enumerator.name]
                enumerator.value = pycparser.c_ast.Constant("int", str(last))

        def visit_EnumeratorList(self, enum_list):
            # Check if we are in a typedef scope
            if self._name is not None:
                value = 0
                mapping = {}
                context = {}
                for enum in enum_list.enumerators:
                    assert type(enum) is pycparser.c_ast.Enumerator

                    # Check if enum has defined a value
                    if enum.value is not None:
                        value = handle_expression(context, enum.value)
                    name = remove_prefix(enum.name)
                    mapping[value] = name

                    # Add reverse lookup as well, ignoring aliases
                    if name not in mapping:
                        mapping[name] = value

                    # Save in the context, so that other entries can refere to
                    # it
                    context[enum.name] = value

                    # Rewrite using a constant
                    enum.value = pycparser.c_ast.Constant("int", str(value))

                    value += 1

                self.enums[self._name] = mapping

                # Clear the scope
                self._name = None

    with open(all_c_preprocessed) as f:
        ast, _, _ = cffi.cparser.Parser()._parse(f.read())

    visitor = EnumVisitor()
    visitor.visit(ast)

    with open(all_c_preprocessed, "w") as f:
        generator = pycparser.c_generator.CGenerator()
        f.write(generator.visit(ast).replace("__dotdotdot__", "foo"))

    return visitor.enums

def generate_wrapper():
    """Force the (re-)generation of the wrapper module for the current LLVM
    installation"""
    global ffi
    global cached_module
    output_path = cached_module
    ffi = FFI()

    libs, ffi_code, enums = parse_headers()

    if len(libs) == 0:
        raise ValueError("No valid LLVM libraries found' \
            ', LLVM must be built with BUILD_SHARED_LIBS")

    classes = defaultdict(list)
    global_functions = []
    constants = []

    # Loop over all the LLVM libraries
    for _, library_name, library in libs:
        # Loop over all the methods we identified with cffi, not all of them
        # will actually be available
        for name in dir(library):
            # A library contains only some methods, find out which ones
            fail = False
            try:
                field = getattr(library, name)
            except AttributeError:
                fail = True

            if not fail and type(field) is int:
                constants.append((name, field))

            # Is this a usable function?
            if not fail and isinstance(field, FFI.CData):
                # Is the first argument an LLVM object? Did we ever see it
                # before?
                prototype = ffi.typeof(field)
                args = prototype.args
                if len(args) > 0 and args[0].kind == "pointer":
                    arg0_type = args[0].item
                    if (arg0_type.kind == "struct"
                        and is_llvm_type(arg0_type.cname)):

                        if not [1 for x in classes[arg0_type.cname]
                                if x[1] == name]:
                            # Associate to the name of the LLVM object a tuple
                            # containing the library name, the method name and
                            # the function prototype
                            classes[arg0_type.cname].append((library_name,
                                                             name,
                                                             prototype))
                        continue

                # It doesn't fit any class
                if not [1 for x in global_functions if x[1] == name]:
                    global_functions.append((library_name, name, prototype))

    with open(output_path, "w") as output_file:
        def write(string):
            output_file.write(string + "\n")

        # Print file header
        write(ffi_code)
        write("from cffi import FFI")
        write("""
class LLVMException(Exception):
    pass
""")
        for library_path, library_name, library in libs:
            write("""{} = ffi.dlopen("{}")""".format(library_name,
                                                     library_path))

        # Create all the classes
        for key, value in classes.items():
            class_name = remove_llvm_prefix(key)

            # Each class is a wrapper for a pointer to a pointer to an LLVM
            # object: when a pointer is passed to a function use `in_ptr` (which
            # dereferences it), when you want to use it as an out argument using
            # `out_ptr` instead (which returns a **)
            write(
"""
class {class_name}(object):
    def __new__(cls, value=None):
        if value == ffi.NULL:
            return None
        return super({class_name}, cls).__new__(cls)

    def __init__(self, value=None):
        self.ptr = ffi.new("{key} **")
        if value is not None:
            self.ptr[0] = value

    def __hash__(self):
        return hash(self.ptr[0])

    def __eq__(self, other):
        return self.__hash__() == other.__hash__()

    def in_ptr(self):
        if self.ptr[0] == ffi.NULL:
            raise RuntimeError("in_ptr called on uninitialized object")
        return self.ptr[0]

    def out_ptr(self):
        if self.ptr[0] != ffi.NULL:
            raise RuntimeError(("Passing an already initialized object as an " +
                                "out parameter"))
        return self.ptr""".format(class_name=class_name, key=key))

            # Create a dictionary for properties create function will populate
            # it
            properties = defaultdict(lambda: (("", "None"), ("", "None")))

            for library, name, prototype in value:
                write(create_function(library,
                                      name,
                                      prototype,
                                      class_name,
                                      properties,
                                      classes))

            # Create the properties
            write("")
            for name, ((getter_llvm, getter),
                       (setter_llvm, setter)) in properties.items():
                if keyword.iskeyword(name):
                    name += "_"

                docstring = "\"\"\"See "
                docstring += getter_llvm
                if getter_llvm and setter_llvm:
                    docstring += " and "
                docstring += setter_llvm
                docstring += "\"\"\""

                write("""    {} = property({}, {}, doc={})""".format(name,
                                                                     getter,
                                                                     setter,
                                                                     docstring))

        # Print global functions
        write("\nif True:")
        for library, name, prototype in global_functions:
            write(create_function(library, name, prototype))

        # Print numeric constants
        for name, value in constants:
            if name.startswith("LLVM") and not name.startswith("LLVM_"):
                name = name[4:]
            write("{} = {}".format(name, str(value)))

        # Print enum conversion methods
        for name, values in enums.items():
            write(
"""
{} = {}
""".format(name, values))

unsigned_ints = set(("unsigned", "unsigned int", "unsigned long"))

# Add to PATH the output of llvm-config --bin-dir
search_paths = os.environ.get("PATH", os.defpath)
llvm_config = find_program("LLVM_CONFIG", ["llvm-config"])
search_paths = run_llvm_config(["--bindir"]) + (search_paths and (os.pathsep + search_paths))

cache_dir = appdirs.user_cache_dir('llvmcpy')
version = run_llvm_config(["--version"])
to_hash = llvm_config.encode("utf-8")
hasher = hashlib.sha256()
hasher.update(to_hash)
cache_dir = os.path.join(cache_dir, hasher.hexdigest() + "-" + version)
cached_module = os.path.join(cache_dir, "llvmcpyimpl.py")
if not os.path.exists(cached_module):
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    generate_wrapper()
sys.path.insert(0, cache_dir)
from llvmcpyimpl import *
del sys.path[0]
