// tests/fixtures/completion.sv
//
// Completion test fixture — exercises all major completion contexts.
// Each marked position documents what completions should appear.
//
// Compile with: cross_file/pkg.sv (for package import testing)
//
// Notation: COMPLETE@<line>:<col> describes expected completions at that position.

import common_pkg::*;

module completion_test #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 4
) (
    input  logic             clk,
    input  logic             rst_n,
    input  data_t            data_in,
    input  cmd_t             cmd,
    output data_t            data_out,
    output logic             valid
);

    // --- Signals for identifier completion testing ---
    data_t            internal_reg;
    data_t            pipeline_stage [DEPTH];
    logic             ready;
    data_t            processed_data;

    // COMPLETE@33:0 (identifier, manual invoke at statement level)
    //   Expected: internal_reg, pipeline_stage, ready, processed_data,
    //             data_in, data_out, clk, rst_n, cmd, valid, WIDTH, DEPTH
    //   Should include: signals, ports, parameters visible in this scope
    //   Should NOT include: symbols from other modules

    // --- Module instantiation for port completion testing ---
    data_processor u_proc (
        .clk      (clk),
        .rst_n    (rst_n),
        .data_in  (data_in),
        // COMPLETE@42:9 (port connection, after typing '.')
        //   Expected: cmd, data_out, valid (unconnected ports)
        //   Should NOT include: clk, rst_n, data_in (already connected)
        .cmd      (cmd),
        .data_out (processed_data),
        .valid    (ready)
    );

    // --- Package member completion testing ---
    // COMPLETE@49:17 (package member, after typing 'common_pkg::')
    //   Expected: DATA_WIDTH, data_t, cmd_t, CMD_NOP, CMD_READ,
    //             CMD_WRITE, CMD_RESET, is_valid
    logic [common_pkg::DATA_WIDTH-1:0] wide_data;

    // --- System task completion testing ---
    initial begin
        // COMPLETE@55:9 (system task, after typing '$')
        //   Expected: $display, $finish, $clog2, $bits, etc.
        $display("test: %0d", WIDTH);
    end

    // --- Identifier completion inside always block (inner scope) ---
    always_comb begin
        logic temp_val;  // local to this block
        // COMPLETE@63:8 (identifier, manual invoke inside always_comb)
        //   Expected: temp_val (local, sort_group=0),
        //             internal_reg, ready, etc. (module scope, sort_group=1)
        //             data_t, cmd_t (imported, sort_group=2)
        data_out = internal_reg;
    end

    // --- Module name completion testing ---
    // COMPLETE@69:4 (module name, at statement level suitable for instantiation)
    //   Expected: counter, data_processor, shift_register, completion_test
    //   (all module definitions in the compilation)

    // --- Always block with references for document highlight testing ---
    always_ff @(posedge clk or negedge rst_n) begin
        // HIGHLIGHT@internal_reg: should highlight all occurrences in this file
        //   Declaration (line 27), usage here, usage in always_comb above
        if (!rst_n) begin
            internal_reg <= '0;
            valid <= 1'b0;
        end else begin
            internal_reg <= data_in;
            valid <= ready;
        end
    end

endmodule
