def get_module_source(llvm):
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

    llvm_major = int(llvm.version.split(".", 1)[0])
    if llvm_major >= 7:
        module_source += """
!llvm.module.flags = !{!0}
!0 = !{ i32 4, !"foo", i32 42 }"""

    return module_source
