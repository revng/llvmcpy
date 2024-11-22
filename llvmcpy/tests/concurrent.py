import sys
from llvmcpy import LLVMCPy
from . import get_module_source


def main():
    handlers = []
    data = []

    for bin_ in sys.argv[1:]:
        llvm = LLVMCPy(bin_)
        handlers.append(llvm)

        ir = get_module_source(llvm)
        buffer = llvm.create_memory_buffer_with_memory_range_copy(ir, len(ir), "example")
        module = llvm.get_global_context().parse_ir(buffer)
        for function in module.iter_functions():
            for bb in function.iter_basic_blocks():
                for instruction in bb.iter_instructions():
                    data.append(instruction)


if __name__ == "__main__":
    main()
