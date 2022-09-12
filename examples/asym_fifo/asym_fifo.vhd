--===============================================================================================--
--! @file              asym_fifo.vhd
--! @brief             Asymmetric FIFO
--! @author            Kamyar Mohajerani
--! @copyright         Copyright (c) 2022
--! @license           Solderpad Hardware License v2.1 ([SHL-2.1](https://solderpad.org/licenses/SHL-2.1/))
--!                    
--! @vhdl              VHDL 2008, and later
--!
--! @details           Can be used as regular FIFO, Asymmetric FIFO (enqueue/write width != dequeue/read width), SIPO, or PISO
--!                    using dual-port syncrhonous-read memory (read data available on the next clock edge)
--!                    Following AMD/Xilinx/Vivado HDL coding styles for SDP BRAM inference
--!
--! @note              read/write widths and capacity MUST be powers of 2
--===============================================================================================--

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity ASYM_FIFO is
    generic(
        G_WR_W      : positive := 4;    -- write (enqueue) width in bits, must be powers of 2
        G_RD_W      : positive := 16;   -- read (dequeue) width in bits, must be powers of 2
        G_CAPACITY  : positive := 4096; -- memory capacity in bits, must be powers of 2
        G_BIGENDIAN : boolean  := TRUE
    );

    port(
        clk       : in  std_logic;
        rst       : in  std_logic;
        -- Enqueue
        enq_data  : in  std_logic_vector(G_WR_W - 1 downto 0);
        enq_valid : in  std_logic;
        enq_ready : out std_logic;
        -- Dequeue
        deq_data  : out std_logic_vector(G_RD_W - 1 downto 0);
        deq_valid : out std_logic;
        deq_ready : in  std_logic
    );

end ASYM_FIFO;

architecture RTL of ASYM_FIFO is
    --! Returns the number of bits required to represet values less than n (0 to n - 1 inclusive)
    function log2ceil(n : natural) return natural is
        variable pow2 : positive := 1;
        variable r    : natural  := 0;
    begin
        while n > pow2 loop
            pow2 := pow2 * 2;
            r    := r + 1;
        end loop;
        return r;
    end function;

    -- whether `n` is a power of 2
    function is_pow_2(n : natural) return boolean is
    begin
        return 2 ** log2ceil(n) = n;
    end function;

    function to_int01(S : unsigned) return natural is
    begin
        return to_integer(to_01(S));
    end function;

    function slv_chunk(slv : std_logic_vector; chunk_width, i : natural) return std_logic_vector is
        constant NUM_CHUNKS : natural := slv'length / chunk_width;
    begin
        if G_BIGENDIAN then
            return slv((NUM_CHUNKS - i) * chunk_width - 1 downto (NUM_CHUNKS - i - 1) * chunk_width);
        else
            return slv((i + 1) * chunk_width - 1 downto i * chunk_width);
        end if;
    end function;

    constant WR_RD_LOG2 : natural := log2ceil(G_WR_W / G_RD_W);
    constant RD_WR_LOG2 : natural := log2ceil(G_RD_W / G_WR_W);
    constant MEM_DEPTH  : natural := G_CAPACITY / minimum(G_WR_W, G_RD_W);

    type T_RAM is array (0 to MEM_DEPTH - 1) of std_logic_vector(minimum(G_WR_W, G_RD_W) - 1 downto 0);

    -- NOTE: using log2 so it works for corner cases as well
    -- NOTE: not using VHDL 2008+ unconstrained arrays for compatibility
    type READ_WORDS_T is array (0 to 2 ** RD_WR_LOG2 - 1) of std_logic_vector(G_WR_W - 1 downto 0);

    function slva_concat(slva : READ_WORDS_T) return std_logic_vector is
        -- requires slva'length >= 1
        constant EL_WIDTH   : positive := slva(0)'length;
        constant NUM_CHUNKS : positive := slva'length;
        constant RET_WIDTH  : positive := EL_WIDTH * NUM_CHUNKS;
        variable ret        : std_logic_vector(RET_WIDTH - 1 downto 0);
    begin
        for i in slva'range loop
            if G_BIGENDIAN then
                ret((i + 1) * EL_WIDTH - 1 downto i * EL_WIDTH) := slva(NUM_CHUNKS - i - 1);
            else
                ret((i + 1) * EL_WIDTH - 1 downto i * EL_WIDTH) := slva(i);
            end if;
        end loop;
        return ret;
    end function;

    --======================================== Memory ==========================================--
    signal ram : T_RAM;

    -- Xilinx/Vivado:
    attribute ram_style : string;
    attribute ram_style of ram : signal is "block"; -- also try: "mixed", "ultra"

    --======================================= Registers =========================================--
    signal is_empty          : boolean; -- := TRUE;
    signal is_full           : boolean; -- := FALSE;
    signal dequeued_is_valid : boolean; -- := FALSE;
    signal rd_ptr            : unsigned(log2ceil(G_CAPACITY / G_RD_W) - 1 downto 0);
    signal wr_ptr            : unsigned(log2ceil(G_CAPACITY / G_WR_W) - 1 downto 0);
    signal out_reg           : std_logic_vector(G_RD_W - 1 downto 0);

    --========================================= Wires ===========================================--
    signal next_rd_ptr               : unsigned(rd_ptr'range);
    signal next_wr_ptr               : unsigned(wr_ptr'range);
    signal do_enq, do_deq, overlap   : boolean;
    signal almost_empty, almost_full : boolean;
    signal can_deq                   : boolean;
    signal read_data                 : std_logic_vector(G_RD_W - 1 downto 0);

begin

    assert is_pow_2(G_RD_W) and is_pow_2(G_WR_W) and is_pow_2(G_CAPACITY)
    report "G_RD_W, G_WR_W, and G_CAPACITY must be powers of 2"
    severity FAILURE;

    assert G_CAPACITY >= G_WR_W and G_CAPACITY >= G_RD_W
    report "G_CAPACITY should not be smaller than G_WR_W or G_RD_W"
    severity FAILURE;

    next_rd_ptr <= rd_ptr + 1;          -- mod (2 ** DEPTH_BITS)
    next_wr_ptr <= wr_ptr + 1;          -- mod (2 ** DEPTH_BITS)

    GEN_OVERLAP : if rd_ptr'length = 0 or wr_ptr'length = 0 generate
        overlap <= TRUE;
    else generate
        overlap <= rd_ptr(rd_ptr'length - 1 downto WR_RD_LOG2) = wr_ptr(wr_ptr'length - 1 downto RD_WR_LOG2);
    end generate;

    almost_empty <= next_rd_ptr & (RD_WR_LOG2 - 1 downto 0 => '0') = wr_ptr & (WR_RD_LOG2 - 1 downto 0 => '0');
    almost_full  <= next_wr_ptr & (WR_RD_LOG2 - 1 downto 0 => '0') = rd_ptr & (RD_WR_LOG2 - 1 downto 0 => '0');

    assert (wr_ptr'length - RD_WR_LOG2) = (rd_ptr'length - WR_RD_LOG2) severity FAILURE;

    do_enq    <= enq_valid = '1' and enq_ready = '1';
    deq_valid <= '1' when dequeued_is_valid else '0';
    do_deq    <= can_deq and (not dequeued_is_valid or deq_ready = '1');

    process(clk) is
    begin
        if rising_edge(clk) then
            if rst = '1' then
                is_empty          <= TRUE;
                is_full           <= FALSE;
                dequeued_is_valid <= FALSE;
                rd_ptr            <= (others => '0');
                wr_ptr            <= (others => '0');
            else
                if do_enq then
                    if not do_deq and almost_full then
                        is_full <= TRUE;
                    end if;
                    is_empty <= FALSE;
                    wr_ptr   <= next_wr_ptr;
                end if;
                if do_deq then
                    dequeued_is_valid <= TRUE;
                    if not do_enq and almost_empty then
                        is_empty <= TRUE;
                    end if;
                    is_full           <= FALSE;
                    rd_ptr            <= next_rd_ptr;
                else
                    if deq_valid = '1' and deq_ready = '1' then
                        dequeued_is_valid <= FALSE;
                    end if;
                end if;
            end if;
        end if;
    end process;

    GEN_READ_WIDER : if G_RD_W >= G_WR_W generate
        signal read_words : READ_WORDS_T;
    begin

        can_deq   <= is_full or not overlap;
        enq_ready <= '0' when is_full else '1';

        read_data <= slva_concat(read_words);

        process(clk) is
        begin
            if rising_edge(clk) then
                -- Write
                if do_enq then
                    ram(to_int01(wr_ptr)) <= enq_data;
                end if;
                -- Read
                if do_deq then
                    for i in read_words'range loop
                        read_words(i) <= ram(to_int01(rd_ptr & to_unsigned(i, RD_WR_LOG2)));
                    end loop;
                end if;
                out_reg <= read_data;
            end if;
        end process;
    end generate;

    GEN_WRITE_WIDER : if G_RD_W < G_WR_W generate -- enq is wider than deq

        can_deq   <= not is_empty;
        enq_ready <= '1' when (is_empty or not overlap) else '0';

        process(clk) is
        begin
            if rising_edge(clk) then
                -- Write
                if do_enq then
                    for i in 0 to 2 ** WR_RD_LOG2 - 1 loop
                        ram(to_int01(wr_ptr & to_unsigned(i, WR_RD_LOG2))) <= slv_chunk(enq_data, G_RD_W, i);
                    end loop;
                end if;
                -- Read
                if do_deq then
                    read_data <= ram(to_int01(rd_ptr));
                end if;
                out_reg <= read_data;
            end if;
        end process;
    end generate;

    deq_data <= read_data;

end architecture;
