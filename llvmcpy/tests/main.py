import unittest

from llvmcpy import LLVMCPy
from . import get_module_source

llvm = LLVMCPy()
module_source = get_module_source(llvm)


def load_module(ir):
    context = llvm.get_global_context()
    buffer = llvm.create_memory_buffer_with_memory_range_copy(ir, len(ir), "example")
    return context.parse_ir(buffer)


def get_function_number(ir):
    module = load_module(ir)
    return len(list(module.iter_functions()))


def get_non_existing_basic_block(ir):
    module = load_module(ir)
    first_function = list(module.iter_functions())[0]
    first_basic_block = list(first_function.iter_basic_blocks())[0]
    first_basic_block.get_next().first_instruction()


class TestSuite(unittest.TestCase):
    def test_function_count(self):
        self.assertEqual(get_function_number(module_source), 3)

    def test_null_ptr(self):
        with self.assertRaises(AttributeError):
            get_non_existing_basic_block(module_source)

    def test_resolve_enums(self):
        assert llvm.Opcode[llvm.Switch] == "Switch"
        assert llvm.Opcode["Switch"] == llvm.Switch

    def test_translate_null_ptr_to_none(self):
        module = load_module(module_source)
        first_function = list(module.iter_functions())[0]
        first_basic_block = list(first_function.iter_basic_blocks())[0]
        first_instruction = first_basic_block.first_instruction

        assert first_instruction.is_a_binary_operator() is None

    def test_value_as_key(self):
        module = load_module(module_source)
        function1 = module.get_named_function("function1")
        first_basic_block = function1.get_first_basic_block()
        first_instruction = first_basic_block.get_first_instruction()
        second_instruction = first_instruction.get_next_instruction()
        operand1 = first_instruction.get_operand(0)
        operand2 = second_instruction.get_operand(0)
        dictionary = {}
        dictionary[operand1] = 42
        assert operand2 in dictionary

    def test_sized_string_return(self):
        string = "a\0b\0c"
        value = llvm.md_string(string, len(string))
        self.assertEqual(value.get_md_string(), string)
        self.assertEqual(value.get_md_string(encoding=None), string.encode("ascii"))

    def test_metadata_flags(self):
        if int(llvm.version.split(".", 1)[0]) < 7:
            return
        module = load_module(module_source)
        length = llvm.ffi.new("size_t *")
        metadata_flags = module.copy_module_flags_metadata(length)
        behavior = metadata_flags.module_flag_entries_get_flag_behavior(0)
        key = metadata_flags.module_flag_entries_get_key(0)
        assert behavior == 3
        assert key == "foo"


if __name__ == "__main__":
    unittest.main()
