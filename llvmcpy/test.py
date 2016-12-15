import unittest
from llvmcpy import llvm

module_source = """; ModuleID = 'example.c'
source_filename = "example.c"
target datalayout = "e-m:e-i64:64-f80:128-n8:16:32:64-S128"
target triple = "x86_64-pc-linux-gnu"

; Function Attrs: nounwind uwtable
define i32 @function2() {
  ret i32 42
}

; Function Attrs: nounwind uwtable
define i32 @function1() {
  %1 = call i32 @function2()
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

def get_function_number(ir):
    context = llvm.get_global_context()
    buffer = llvm.create_memory_buffer_with_memory_range_copy(ir,
                                                              len(ir),
                                                              "example")
    module = context.parse_ir(buffer)
    return len(list(module.iter_functions()))

class TestSuite(unittest.TestCase):
    def function_count(self):
        self.assertEqual(get_function_number(module_source), 3)
