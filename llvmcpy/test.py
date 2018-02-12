import unittest
from llvmcpy import llvm

module_source = """; ModuleID = 'example.c'
target datalayout = "e-m:e-i64:64-f80:128-n8:16:32:64-S128"
target triple = "x86_64-pc-linux-gnu"

; Function Attrs: nounwind uwtable
define i32 @function2() {
  ret i32 42
}

; Function Attrs: nounwind uwtable
define i32 @function1() {
  %1 = call i32 @function2()
  %2 = call i32 @function2()
  ret i32 %1
}

; Function Attrs: nounwind uwtable
define i32 @main(i32, i8**) {
  %3 = alloca i32, align 4
  %4 = alloca i32, align 4
  %5 = alloca i8**, align 8
  store i32 0, i32* %3, align 4
  store i32 %0, i32* %4, align 4
  store i8** %1, i8*** %5, align 8
  %6 = call i32 @function1()
  ret i32 %6
}
"""

def load_module(ir):
    context = llvm.get_global_context()
    buffer = llvm.create_memory_buffer_with_memory_range_copy(ir,
                                                              len(ir),
                                                              "example")
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
        assert llvm.Opcode[llvm.Switch] == 'Switch'
        assert llvm.Opcode['Switch'] == llvm.Switch

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

if __name__ == '__main__':
    unittest.main()
