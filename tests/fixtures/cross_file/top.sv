// tests/fixtures/cross_file/top.sv
//
// Top-level module that instantiates sub-modules and uses the package.
// This is the primary cross-file resolution test.
//
// Expected LSP behavior:
//   - go-to-def on "data_processor" (line 20): jump to sub.sv module declaration
//   - go-to-def on "common_pkg" (line 10): jump to pkg.sv package declaration
//   - go-to-def on "data_t" (line 13): jump to pkg.sv typedef
//   - go-to-def on "u_proc" (line 20): this IS the instance, hover should show
//     "instance of data_processor"
//   - hover on "top_design" (line 12): show port list
//   - hover on "u_proc" (line 20): show "instance of data_processor"
//   - find-references on "internal_data": lines 13, 22, 30
//   - documentSymbol: module "top_design", ports, signals, instance "u_proc"

import common_pkg::*;

module top_design (
    input  logic  clk,
    input  logic  rst_n,
    input  data_t data_in,
    input  cmd_t  cmd_in,
    output data_t data_out,
    output logic  data_valid
);

    data_t internal_data;
    logic  internal_valid;

    data_processor u_proc (
        .clk      (clk),
        .rst_n    (rst_n),
        .data_in  (data_in),
        .cmd      (cmd_in),
        .data_out (internal_data),
        .valid    (internal_valid)
    );

    // Pipeline register on output
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_out   <= '0;
            data_valid <= 1'b0;
        end else begin
            data_out   <= internal_data;
            data_valid <= internal_valid;
        end
    end

endmodule
