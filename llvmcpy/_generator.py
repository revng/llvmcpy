import fnmatch
import keyword
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, List, MutableMapping, Optional, Tuple, Union

import cffi
import pycparser
import pycparser.c_generator
from cffi import FFI

Properties = MutableMapping[str, Tuple[Tuple[str, str], Tuple[str, str]]]
Classes = MutableMapping[str, List[Tuple[str, str, Any]]]
EnumMap = MutableMapping[Union[int, str], Union[int, str]]

unsigned_ints = {"unsigned", "unsigned int", "unsigned long"}


def handle_enums(all_c_preprocessed: Path) -> MutableMapping[str, EnumMap]:
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

    def remove_prefix(name: str) -> str:
        if name.startswith("LLVM") and not name.startswith("LLVM_"):
            return name[4:]
        return name

    def handle_expression(variables, expression) -> int:
        expression_type = type(expression)
        if expression_type is pycparser.c_ast.Constant:
            return int(expression.value.rstrip("U"), 0)
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
            self._name: Optional[str] = None
            self.enums: MutableMapping[str, EnumMap] = {}

        def visit_Typedef(self, typedef) -> None:
            self._name = remove_prefix(typedef.name)
            self.generic_visit(typedef)

        def visit_EnumeratorList(self, enum_list) -> None:
            # Check if we are in a typedef scope
            if self._name is not None:
                value = 0
                mapping: MutableMapping[Union[int, str], Union[int, str]] = {}
                context: MutableMapping[str, int] = {}
                for enum in enum_list.enumerators:
                    assert isinstance(enum, pycparser.c_ast.Enumerator)

                    # Check if enum has defined a value
                    if enum.value is not None:
                        value = handle_expression(context, enum.value)
                    name = remove_prefix(enum.name)
                    mapping[value] = name

                    # Add reverse lookup as well, ignoring aliases
                    if name not in mapping:
                        mapping[name] = value

                    # Save in the context, so that other entries can refer to it
                    context[enum.name] = value

                    # Rewrite using a constant
                    enum.value = pycparser.c_ast.Constant("int", str(value))  # type: ignore

                    value += 1

                self.enums[self._name] = mapping

                # Clear the scope
                self._name = None

    ast, _, _ = cffi.cparser.Parser()._parse(all_c_preprocessed.read_text())  # type: ignore

    visitor = EnumVisitor()
    visitor.visit(ast)

    generator = pycparser.c_generator.CGenerator()
    all_c_preprocessed.write_text(generator.visit(ast).replace("__dotdotdot__", "foo"))

    return visitor.enums


class Generator:
    def __init__(self, cpp: str, libraries: List[Path], include_dir: Path):
        self.cpp = cpp
        self.libraries = libraries
        self.include_dir = include_dir
        self.ffi = FFI()

    def is_llvm_type(self, name: str) -> bool:
        return name.startswith("struct LLVM") or name.startswith("LLVM")

    def remove_llvm_prefix(self, name: str) -> str:
        assert self.is_llvm_type(name)
        if name.startswith("struct "):
            name = name[len("struct ") :]
        name = name[len("LLVM") :]
        if name.startswith("Opaque"):
            name = name[len("Opaque") :]
        return name

    def to_python_case(self, name: str) -> str:
        """Convert "GetLLVMFunctionID" to a more pythonic "get_llvm_function_id" """

        # If it's all upper cases, simply return the lower case version
        if name.isupper():
            return name.lower()

        result = ""
        # When the case in the input changes, emit a _
        for prev, cur, next in zip("a" + name[:-1], name, name[1:] + "a"):
            if (prev.islower() and cur.isupper()) or (next.islower() and cur.isupper()):
                result += "_"
            result += cur.lower()

        # If the string ends with capital letters, remove the last _
        if name[-2:].isupper():
            result = result[:-2] + result[-1:]

        # Discard the initial _
        return result[1:]

    def normalize_name(self, original_class_name: Optional[str], original_name: str) -> str:
        """Normalizes the case and remove the name of the class from the method name
        in several common cases (e.g., Value.get_value_name => Value.get_name)"""

        # BasicBlockAsValue -> GetBasicBlockAsValue
        if original_class_name is not None and original_name.startswith(original_class_name):

            to_skip = len(original_class_name)
            return self.normalize_name(original_class_name, original_name[to_skip:])

        name = self.to_python_case(original_name)
        class_name = "" if original_class_name is None else self.to_python_case(original_class_name)

        prefix_get = "get_" + class_name + "_"
        prefix_set = "set_" + class_name + "_"
        if name.startswith(prefix_get):
            # Remove Value from GetValueName
            name = "get_" + name[len(prefix_get) :]
        elif name.startswith(prefix_set):
            # Remove Value from SetValueName
            name = "set_" + name[len(prefix_set) :]
        elif class_name and name.endswith("_in_" + class_name):
            # Remove InContext from ParseIrInContext
            name = name[: -len(class_name) - 4]
        elif class_name and name.endswith("_" + class_name):
            # Remove Value from DumpValue
            name = name[: -len(class_name) - 1]

        return name

    def create_function(
        self,
        library: str,
        name: str,
        prototype,
        class_name: Optional[str] = None,
        properties: Optional[Properties] = None,
        classes: Optional[Classes] = None,
    ):
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
                if (
                    pointee.kind == "pointer"
                    and pointee.item.kind == "struct"
                    and self.is_llvm_type(pointee.item.cname)
                ):
                    # LLVM object **: the function is returning a reference to an
                    # object or it's an array
                    arguments.append(
                        (
                            "([x.in_ptr() for x in arg{}] "
                            + "if type(arg{}) is list "
                            + "else arg{}.out_ptr())"
                        ).format(index, index, index)
                    )
                    out_args.append(index)
                elif (
                    pointee.kind == "pointer"
                    and pointee.item.kind == "primitive"
                    and pointee.item.cname == "char"
                ):
                    # char **: the function is returning a string
                    arguments.append(f"arg{index}")
                    out_strings.append(index)
                elif pointee.kind == "struct" and self.is_llvm_type(pointee.cname):
                    # LLVM object *: the function takes is taking an LLVM object
                    arguments.append(f"arg{index}.in_ptr()")
                elif pointee.kind == "primitive" and pointee.cname == "char":
                    # char *: TODO
                    arguments.append(f"""encode_string(arg{index})""")
                elif pointee.kind == "primitive":
                    # int *: TODO
                    arguments.append(f"arg{index}")
                elif pointee.kind == "void":
                    # void *: TODO
                    arguments.append(f"arg{index}")
                else:
                    print(pointee)
                    assert False
            elif arg_type.kind == "primitive" or arg_type.kind == "enum":
                # Enumeration
                arguments.append(f"arg{index}")
            elif arg_type.kind == "function":
                # Function pointer
                arguments.append(f"arg{index}")
            else:
                print(prototype)
                assert False

        # Build arguments for the function call
        function_arguments: List[Optional[str]] = [
            f"arg{x}" for x in range(len(effective_arguments))
        ]

        # Compute pythonic name
        method_name = self.normalize_name(class_name, name[4:])

        # If the method starts with get_ or set_ also create the appropriate
        # property
        if is_class_method:
            assert properties is not None
            if method_name.startswith("get_") and len(function_arguments) == 0:
                properties[method_name[4:]] = (
                    (name, method_name),
                    properties[method_name[4:]][1],
                )
            elif method_name.startswith("set_") and len(function_arguments) == 1:
                properties[method_name[4:]] = (
                    properties[method_name[4:]][0],
                    (name, method_name),
                )

        # Function to compute header of the generated function
        def header():
            zeroth_argument = ["self"] if is_class_method else []
            # Discard None arguments, they have been removed
            args = [arg for arg in function_arguments if arg is not None]
            function_arguments_str = ", ".join(zeroth_argument + list(args))
            return f'def {method_name}({function_arguments_str}):\n    """See {name}"""\n'

        # Function to compute the function call
        def call():
            zeroth_argument = ["self.in_ptr()"] if is_class_method else []
            arguments_str = ", ".join(zeroth_argument + arguments)
            return f"{library}.{name}({arguments_str})"

        # Prepare creation of the function body handling special cases
        return_type = prototype.result
        last_arg = effective_arguments[-1] if effective_arguments else None

        # Look for pairs of pointer to LLVM objects followed by an integer: they
        # often represent pointer-to-first-element + length pairs describing an
        # array of LLVM objects.
        for out_arg in out_args:
            # If it's not the last argument and the next is an integer
            if (
                (len(effective_arguments) > out_arg + 1)
                and (effective_arguments[out_arg + 1].kind == "primitive")
                and (effective_arguments[out_arg + 1].cname == "unsigned int")
            ):

                # Replace the argument with the length of the previous argument
                arguments[out_arg + 1] = f"len(arg{out_arg})"

                # Remove the function argument, we know how to compute it
                function_arguments[out_arg + 1] = None

        # Special case: the function returns a boolean and has either a single LLVM
        # object out argument or an out string argument. In this case we will remove
        # the out argument (if present), create a temporary object and return in
        # it. More over in case of error we'll throw an exception, possibly with an
        # appropriate error message.
        has_out_arg = len(out_args) == 1
        has_error_message = (len(out_strings) == 1) and (out_strings[0] == len(arguments) - 1)

        if (
            return_type.kind == "primitive"
            and return_type.cname == "int"
            and (has_out_arg or has_error_message)
        ):

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
            error_message = '"Error"'
            if has_error_message:
                result += """    error_str = ffi.new("char **")""" + "\n"
                error_message = "ffi.string(error_str[0])"

            result_type = "None"
            if has_out_arg:
                # Print the function body: first create a temporary object we will
                # return, then call the function replacing the out argument with
                # that object, take the boolean result and if there's an error throw
                # an exception
                result_type = prototype.args[out_arg + skip_args].item.item.cname
                result_type = self.remove_llvm_prefix(result_type)
                result_type += "()"

            result += f"""    result = {result_type}
    failure = {call()}
    if failure != 0:
        raise LLVMException({error_message})
    return result"""

        elif (
            return_type.kind == "pointer"
            and return_type.item.kind == "primitive"
            and return_type.item.cname == "char"
            and last_arg
            and last_arg.kind == "pointer"
            and last_arg.item.kind == "primitive"
            and last_arg.item.cname in unsigned_ints
        ):

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
            result += f"""    length_buffer = ffi.new("{last_arg.item.cname}[1]")
    ptr = {call()}
    length = length_buffer[0]
    raw_bytes = ffi.unpack(ptr, length)
    return raw_bytes.decode(encoding) if encoding else raw_bytes
    """

        else:
            # Regular case

            # Print function header
            result += header()

            # Handle common return types
            if return_type.kind == "pointer":
                pointee = return_type.item

                # Are we returning an LLVM object? Wrap it in the appropriate class
                if pointee.kind == "struct" and self.is_llvm_type(pointee.cname):
                    return_type_name = self.remove_llvm_prefix(pointee.cname)
                    result += f"    return {return_type_name}({call()})"

                elif pointee.kind == "primitive" and pointee.cname == "char":
                    # Returning a char **, wrap it as a Python string
                    result += f"    return ffi.string({call()})"
                else:
                    # All the rest
                    result += f"    return {call()}"
            else:
                # All the rest
                result += f"    return {call()}"

        # Generate pythonic way to iterate over list of objects (e.g., functions in
        # a module)
        #
        # We need: LLVMGetFirstSomething, with a single argument (self), returning a
        # pointer to an LLVM object, that has a corresponding LLVMGetNextSomething
        # which takes a Something object and returns a Something object
        if (
            is_class_method
            and name.startswith("LLVMGetFirst")
            and len(prototype.args) == 1
            and return_type.kind == "pointer"
            and return_type.item.kind == "struct"
            and self.is_llvm_type(return_type.item.cname)
        ):

            full_iterated_type_name = return_type.item.cname
            iterated_type_name = self.remove_llvm_prefix(full_iterated_type_name)
            iterated_name = name[len("LLVMGetFirst") :]

            # Check if we have a Something class
            assert classes is not None
            if full_iterated_type_name in classes:
                # Look for the LLVMGetNextSomething method
                for library, name, prototype in classes[full_iterated_type_name]:
                    # Check if the prototype is what we expect
                    if (
                        name == "LLVMGetNext" + iterated_name
                        and len(prototype.args) == 1
                        and prototype.args[0] == return_type
                        and prototype.result == return_type
                    ):

                        # OK, we can emit the generator functiono to iterate over
                        # Something
                        docstring = '"""See LLVMGetFirst{} and {}"""'
                        docstring = docstring.format(iterated_name, name)
                        result += f"""

def iter_{self.normalize_name(class_name, iterated_name)}s(self):
    {docstring}
    next = self.{method_name}()
    while next is not None:
        yield next
        next = next.{self.normalize_name(iterated_type_name, name[4:])}()"""

        return result

    def clean_include_file(self, in_path: Path) -> None:
        """Clean the LLVM-C API headers files for parsing by CFFI: remove standard
        includes and static inline functions"""
        header_blacklist = [
            "llvm/Support/DataTypes.h",
            "llvm-c/DataTypes.h",
            "llvm-c/blake3.h",
            "math.h",
            "stddef.h",
            "cstddef",
            "sys/types.h",
            "stdbool.h",
        ]
        out_path = Path(str(in_path) + ".filtered")

        with in_path.open("r", encoding="utf8") as in_file, out_path.open(
            "w", encoding="utf8"
        ) as out_file:
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

    def parse_headers(
        self,
    ) -> (Tuple[List[Tuple[Path, str, Any]], str, MutableMapping[str, EnumMap]]):
        """Parse the header files of the LLVM-C API and produce a list of libraries
        and the CFFI cached data"""

        def recursive_chmod(path: Path):
            path.chmod(0o700)
            for dirpath_str, _, filenames in os.walk(str(path)):
                dirpath = Path(dirpath_str)
                dirpath.chmod(0o700)
                for filename in filenames:
                    (dirpath / filename).chmod(0o600)

        # Create a temporary directory in which we will copy the headers and adapt
        # them a little for CFFI parsing
        temp_directory = Path(tempfile.mkdtemp())
        try:
            llvm_path = temp_directory / "llvm"
            llvm_path.mkdir()
            llvm_c_path = temp_directory / "llvm-c"
            shutil.copytree(self.include_dir / "llvm-c", llvm_c_path)
            shutil.copytree(
                self.include_dir / "llvm" / "Config", temp_directory / "llvm" / "Config"
            )

            recursive_chmod(temp_directory)

            # Find and adapt all the header files
            include_files = []
            for root_str, _, filenames in os.walk(llvm_c_path):
                root = Path(root_str)
                for filename in fnmatch.filter(filenames, "*.h"):
                    if filename != "DataTypes.h" and filename != "blake3.h":
                        header_path = root / filename
                        include_files.append(str(header_path.relative_to(temp_directory)))
                        self.clean_include_file(header_path)

            (temp_directory / "llvm-c" / "Deprecated.h").write_text(
                """
#ifndef LLVM_C_DEPRECATED_H
#define LLVM_C_DEPRECATED_H
#endif /* LLVM_C_DEPRECATED_H */
# define LLVM_ATTRIBUTE_C_DEPRECATED(decl, message) decl
"""
            )

            blake3_h = temp_directory / "llvm-c" / "blake3.h"
            if blake3_h.exists():
                blake3_h.unlink()

            # Create all.c, a C file including all the headers
            all_c = """
typedef long unsigned int size_t;
typedef int off_t;
"""
            all_c += '#include "'
            all_c += '"\n#include "'.join(include_files) + '"'
            all_c += "\n"
            all_c_path = temp_directory / "all.c"
            all_c_path.write_text(all_c)

            # Preprocess all.c
            all_c_preprocessed = temp_directory / "all.prep.c"
            subprocess.check_call(
                [
                    self.cpp,
                    "-U__GNUC__",
                    "-I" + str(temp_directory),
                    "-I" + str(self.include_dir),
                    "-E",
                    "-o" + str(all_c_preprocessed),
                    str(all_c_path),
                ]
            )

            # Parse enum definitions
            enums = handle_enums(all_c_preprocessed)

            # Let CFFI parse the preprocessed header
            self.ffi.cdef(all_c_preprocessed.read_text(), override=True)

            # Compile the CFFI data and save them so we can return it
            self.ffi.set_source("ffi", None)  # type: ignore
            self.ffi.compile(str(temp_directory))

            ffi_code = (temp_directory / "ffi.py").read_text()

        finally:
            # Cleanup
            shutil.rmtree(temp_directory)

        # Create a list of the LLVM libraries and dlopen them
        def basename(x: Path) -> str:
            result = x.name
            result = os.path.splitext(result)[0]
            result = result.replace(".", "")
            result = result.replace("-", "")
            return result

        libs = [
            (lib_file, basename(lib_file), self.ffi.dlopen(str(lib_file)))
            for lib_file in self.libraries
        ]

        return libs, ffi_code, enums

    def generate_wrapper(self, output_path: Path) -> None:
        """Force the (re-)generation of the wrapper module for the current LLVM
        installation"""

        libs, ffi_code, enums = self.parse_headers()

        classes: Classes = defaultdict(list)
        global_functions: List[Tuple[str, str, Any]] = []
        constants: List[Tuple[str, int]] = []

        # Loop over all the LLVM libraries
        for _, library_name, library in libs:
            # Loop over all the methods we identified with cffi, not all of them
            # will actually be available
            for name in dir(library):
                # A library contains only some methods, find out which ones
                if hasattr(library, name):
                    field = getattr(library, name)
                    if isinstance(field, int):
                        constants.append((name, field))

                    elif isinstance(field, FFI.CData):
                        # Is this a usable function?
                        # Is the first argument an LLVM object? Did we ever see it
                        # before?
                        prototype = self.ffi.typeof(field)
                        args = prototype.args
                        if len(args) > 0 and args[0].kind == "pointer":
                            arg0_type = args[0].item
                            if arg0_type.kind == "struct" and self.is_llvm_type(arg0_type.cname):

                                if not [1 for x in classes[arg0_type.cname] if x[1] == name]:
                                    # Associate to the name of the LLVM object a tuple
                                    # containing the library name, the method name and
                                    # the function prototype
                                    classes[arg0_type.cname].append((library_name, name, prototype))
                                continue

                        # It doesn't fit any class
                        if not [1 for x in global_functions if x[1] == name]:
                            global_functions.append((library_name, name, prototype))

        with output_path.open("w", encoding="utf8") as output_file:

            def write(string):
                output_file.write(string + "\n")

            def write_indented(string: str, indent: int):
                for line in string.splitlines():
                    write(" " * indent + line)

            # Print file header
            write(ffi_code)
            write(
                """
from cffi import FFI

class LLVMException(Exception):
    pass

def encode_string(input_):
    assert isinstance(input_, (bytes, str))
    return input_ if isinstance(input_, bytes) else input_.encode("utf-8")
"""
            )
            for library_path, library_name, library in libs:
                write(f"""{library_name} = ffi.dlopen("{library_path}")""")

            # Create all the classes
            for key, value in classes.items():
                class_name = self.remove_llvm_prefix(key)

                # Each class is a wrapper for a pointer to a pointer to an LLVM
                # object: when a pointer is passed to a function use `in_ptr` (which
                # dereferences it), when you want to use it as an out argument using
                # `out_ptr` instead (which returns a **)
                write(
                    f"""
class {class_name}:
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
        return self.ptr"""
                )

                # Create a dictionary for properties create function will populate
                # it
                properties: Properties = defaultdict(lambda: (("", "None"), ("", "None")))

                for library, name, prototype in value:
                    write_indented(
                        self.create_function(
                            library, name, prototype, class_name, properties, classes
                        ),
                        4,
                    )

                # Create the properties
                write("")
                for name, (
                    (getter_llvm, getter),
                    (setter_llvm, setter),
                ) in properties.items():
                    if keyword.iskeyword(name):
                        name += "_"

                    docstring = '"""See '
                    docstring += getter_llvm
                    if getter_llvm and setter_llvm:
                        docstring += " and "
                    docstring += setter_llvm
                    docstring += '"""'

                    write(f"""    {name} = property({getter}, {setter}, doc={docstring})""")

            # Print global functions
            for library, name, prototype in global_functions:
                write(self.create_function(library, name, prototype))

            # Print numeric constants
            for constant_name, constant_value in constants:
                if constant_name.startswith("LLVM") and not constant_name.startswith("LLVM_"):
                    constant_name = constant_name[4:]
                write(f"{constant_name} = {constant_value}")

            # Print enum conversion methods
            for enum_name, enum_values in enums.items():
                write(f"\n{enum_name} = {enum_values}\n")
