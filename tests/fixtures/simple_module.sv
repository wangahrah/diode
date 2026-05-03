// tests/fixtures/simple_module.sv
//
// A basic SystemVerilog module exercising fundamental LSP features.
//
// Expected LSP behavior:
//   - documentSymbol: should list module "counter", ports (clk, rst_n, en,
//     count), parameter WIDTH, signal count_next, always_ff block
//   - hover on "counter" (line 14): show port list and parameter
//   - hover on "count" (line 18): show "output logic [WIDTH-1:0]"
//   - hover on "WIDTH" (line 15): show "parameter int, default = 8"
//   - go-to-def on "count_next" (line 30): jump to line 21
//   - find-references on "count_next": lines 21, 24, 26, 30

module counter #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             en,
    output logic [WIDTH-1:0] count
);

    logic [WIDTH-1:0] count_next;

    // Combinational next-state logic
    always_comb begin
        count_next = count + 1'b1;
        if (count_next == '0) begin
            count_next = '1;  // saturate instead of wrapping
        end
    end

    // Sequential register
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= '0;
        end else if (en) begin
            count <= count_next;
        end
    end

endmodule
