// tests/fixtures/cross_file/sub.sv
//
// A sub-module that imports from the package and processes data.
//
// Expected LSP behavior:
//   - go-to-def on "common_pkg" (line 10): jump to pkg.sv package declaration
//   - go-to-def on "data_t" (line 13): jump to pkg.sv typedef
//   - go-to-def on "cmd_t" (line 14): jump to pkg.sv enum typedef
//   - go-to-def on "CMD_NOP" (line 22): jump to pkg.sv enum member
//   - go-to-def on "is_valid" (line 23): jump to pkg.sv function
//   - hover on "data_processor": show port list
//   - hover on "data_in" (line 13): show "input common_pkg::data_t"
//   - find-references on "data_processor": should find instantiation in top.sv

import common_pkg::*;

module data_processor (
    input  logic   clk,
    input  logic   rst_n,
    input  data_t  data_in,
    input  cmd_t   cmd,
    output data_t  data_out,
    output logic   valid
);

    data_t data_reg;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_reg <= '0;
            valid    <= 1'b0;
        end else begin
            valid <= is_valid(cmd);
            case (cmd)
                CMD_NOP:   data_reg <= data_reg;
                CMD_READ:  data_reg <= data_in;
                CMD_WRITE: data_reg <= data_in;
                CMD_RESET: data_reg <= '0;
                default:   data_reg <= data_reg;
            endcase
        end
    end

    assign data_out = data_reg;

endmodule
