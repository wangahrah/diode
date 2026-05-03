// tests/fixtures/cross_file/pkg.sv
//
// A package defining shared types and constants, imported by other files.
//
// Expected LSP behavior:
//   - documentSymbol: package "common_pkg", typedef "data_t",
//     parameter DATA_WIDTH, function "is_valid"
//   - hover on "data_t" (line 15): show "typedef logic [DATA_WIDTH-1:0]"
//   - hover on "DATA_WIDTH" (line 12): show "parameter int, default = 16"
//   - find-references on "common_pkg": should find imports in top.sv and sub.sv
//   - find-references on "data_t": should find usages in sub.sv port list

package common_pkg;

    parameter int DATA_WIDTH = 16;

    typedef logic [DATA_WIDTH-1:0] data_t;

    typedef enum logic [1:0] {
        CMD_NOP  = 2'b00,
        CMD_READ = 2'b01,
        CMD_WRITE = 2'b10,
        CMD_RESET = 2'b11
    } cmd_t;

    function automatic logic is_valid(cmd_t cmd);
        return cmd != CMD_NOP;
    endfunction

endpackage
