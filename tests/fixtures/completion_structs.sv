// tests/fixtures/completion_structs.sv
//
// Completion test fixture for dot-completion contexts:
// struct fields, enum values, and nested types.
//
// Notation: COMPLETE@<line>:<col> describes expected completions at that position.

package struct_pkg;

    typedef struct packed {
        logic [7:0] addr;
        logic [31:0] data;
        logic        valid;
        logic        ready;
    } bus_req_t;

    typedef struct packed {
        logic [31:0] data;
        logic [1:0]  resp;
        logic        valid;
    } bus_resp_t;

    typedef enum logic [2:0] {
        STATE_IDLE   = 3'b000,
        STATE_REQ    = 3'b001,
        STATE_WAIT   = 3'b010,
        STATE_RESP   = 3'b011,
        STATE_DONE   = 3'b100
    } state_t;

endpackage

module struct_completion_test
    import struct_pkg::*;
(
    input  logic clk,
    input  logic rst_n
);

    bus_req_t  request;
    bus_resp_t response;
    state_t    current_state;

    // --- Struct dot-completion testing ---
    always_comb begin
        // COMPLETE@47:20 (dot, after typing 'request.')
        //   Expected: addr, data, valid, ready (struct fields)
        //   Kind: FIELD for each
        request.valid = 1'b1;
        request.addr  = 8'hFF;

        // COMPLETE@52:21 (dot, after typing 'response.')
        //   Expected: data, resp, valid (struct fields)
        response.valid = 1'b1;
    end

    // --- Nested access (response to verify multi-level works) ---
    always_ff @(posedge clk) begin
        if (request.valid && response.valid) begin
            // This tests that dot-completion works in expression context
            // COMPLETE@60:24 (dot, after 'request.' inside if-expression)
            //   Expected: addr, data, valid, ready
        end
    end

    // --- Workspace symbol testing ---
    // WORKSPACE_SYMBOL@"bus_req" → should find bus_req_t in struct_pkg
    // WORKSPACE_SYMBOL@"state" → should find state_t, STATE_IDLE, etc.
    // WORKSPACE_SYMBOL@"struct_completion" → should find this module

endmodule
