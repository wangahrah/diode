// tests/fixtures/bad.sv
//
// A SystemVerilog file with intentional errors for diagnostic testing.
//
// Expected diagnostics:
//   - line 10: undeclared identifier 'nonexistent_signal'
//   - line 11: unknown module 'ghost_module'

module bad_module (
    input logic clk,
    input logic data_in
);

    logic [3:0] result;

    assign result = nonexistent_signal;

    ghost_module u_ghost (.clk(clk));

endmodule
