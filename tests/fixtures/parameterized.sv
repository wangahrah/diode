// tests/fixtures/parameterized.sv
//
// A parameterized module with generate blocks, exercising more complex
// symbol resolution.
//
// Expected LSP behavior:
//   - documentSymbol: module "shift_register", parameter DEPTH/WIDTH,
//     ports (clk, rst_n, din, dout), generate block "gen_stages",
//     instances "stage_reg[i]" (or the generate variable)
//   - hover on "DEPTH" (line 14): show "parameter int, default = 4"
//   - hover on "shift_register" (line 13): show port list with parameters
//   - go-to-def on "stage_reg" inside generate: should resolve to the
//     logic declaration at line 22
//   - find-references on "stage_reg": lines 22, 27, 29, 30, 35

module shift_register #(
    parameter int DEPTH = 4,
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic [WIDTH-1:0] din,
    output logic [WIDTH-1:0] dout
);

    logic [WIDTH-1:0] stage_reg [DEPTH];

    // Generate pipeline stages
    generate
        for (genvar i = 0; i < DEPTH; i++) begin : gen_stages
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    stage_reg[i] <= '0;
                end else begin
                    if (i == 0)
                        stage_reg[i] <= din;
                    else
                        stage_reg[i] <= stage_reg[i-1];
                end
            end
        end
    endgenerate

    assign dout = stage_reg[DEPTH-1];

endmodule
