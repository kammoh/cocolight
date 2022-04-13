--===============================================================================================--
--! @file              SIPO.vhd
--! @brief             Serial-In-Pallell-Out width converter
--! @author            Kamyar Mohajerani
--! @copyright         Copyright (c) 2022
--! @license           Solderpad Hardware License v2.1 https://solderpad.org/licenses/SHL-2.1/
--!                    SPDX Identifier: SHL-2.1
--! @vhdl              Compatible with VHDL 1993, 2002, 2008
--!
--! @details           Universal SIPO implementations covering multiple use-cases
--!
--! @param G_IN_W      Width of input (serial) data
--! @param G_N         Ratio of the width of output data to input data
--! @param G_CHANNELS  Input/output come in G_CHANNELS separate data "channels"
--! @param G_PIPELINED Pipelined, shift-register impl. Data is fully registered
--! @param G_WITH_LAST Support _last I/O signals
--!
--! @note              * All possible value values of G_IN_W, G_N, G_CHANNELS, G_BIGENDIAN and
--!                        G_ASYNC_RSTN are fully supported
--!                    * (G_PIPELINED and (G_WITH_LAST or G_CLEAR_INVALIDS)) is not supported
--!                    * (G_CLEAR_INVALIDS and not G_WITH_LAST) is not supported
--!                      
--===============================================================================================--

library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity SIPO is
    generic(
        --! Input width in bits
        G_IN_W           : positive;
        --! Ratio of output width to input width. Output width is G_N * G_IN_W
        G_N              : positive;
        --! Input and output data are in G_CHANNELS independent "channels".
        --!    Used e.g., in masked LWC implementations
        G_CHANNELS       : positive := 1;
        --! When TRUE, reset is asynchronous and active-low
        G_ASYNC_RSTN     : boolean  := FALSE;
        --! sin_data words will be sequenced in a big-endian order within pout_data (little-endian otherwise)
        G_BIGENDIAN      : boolean  := TRUE;
        --! Pipelined (and not passthrough)
        G_PIPELINED      : boolean  := FALSE;
        --! If FALSE ignores sin_last/pout_last and assume input data sequence always comes in a multiple of G_N
        G_WITH_LAST      : boolean  := FALSE;
        --! zero-out the words in output not filled due to early sin_last. Only effective when G_WITH_LAST = TRUE.
        G_CLEAR_INVALIDS : boolean  := TRUE
    );
    port(
        clk        : in  std_logic;
        rst        : in  std_logic;
        --! Serial Input
        sin_data   : in  std_logic_vector(G_CHANNELS * G_IN_W - 1 downto 0);
        -- last input word. The output will be then ready, even if less than G_IN parts are filled in
        sin_last   : in  std_logic;
        sin_valid  : in  std_logic;
        sin_ready  : out std_logic;
        --! Parallel Output
        pout_data  : out std_logic_vector(G_CHANNELS * G_N * G_IN_W - 1 downto 0);
        pout_last  : out std_logic;
        pout_valid : out std_logic;
        pout_ready : in  std_logic
    );

end entity SIPO;

architecture RTL of SIPO is
begin
    --===========================================================================================--
    GEN_TRIVIAL : if G_N = 1 generate
        -- sin_last is ignored
        pout_data  <= sin_data;
        pout_valid <= sin_valid;
        pout_last  <= sin_last;
        sin_ready  <= pout_ready;
    end generate GEN_TRIVIAL;
    --===========================================================================================--
    GEN_NONTRIVIAL : if G_N > 1 generate
        --! second dimension is constrained to support VHDL 2002
        type T_sin_channels is array (integer range <>) of std_logic_vector(G_IN_W - 1 downto 0);
        --======================================== Functions ====================================--
        function bitmask(b : std_logic; bv : std_logic_vector) return std_logic_vector is
        begin
            return (bv'range => b) and bv;
        end function;

        pure function BUFF_WORDS return natural is
        begin
            if G_PIPELINED then
                return G_N;
            else
                return G_N - 1;
            end if;
        end function;

        --======================================== Constants ====================================--
        constant INIT_MARKER : std_logic_vector(0 to BUFF_WORDS) := (0 => '1', others => '0');

        --========================================== Types ======================================--
        type t_data is array (0 to BUFF_WORDS - 1) of std_logic_vector(G_IN_W - 1 downto 0);
        type t_data_arr is array (0 to G_CHANNELS - 1) of t_data;
        type t_pout_data is array (0 to G_N - 1) of std_logic_vector(G_IN_W - 1 downto 0);
        type t_pout_data_arr is array (0 to G_CHANNELS - 1) of t_pout_data;

        --======================================== Registers ====================================--
        signal data   : t_data_arr;
        -- Fill gauge one-hot marker (shift-register)
        -- size is `BUFF_WORDS+1` bits
        -- bits `0..BUFF_WORDS-1` correspond to the buffer index where the next input is stored.
        -- `marker[BUFF_WORDS]` indicates the buffer is full
        -- initialized with "10...0" @reset
        signal marker : std_logic_vector(0 to BUFF_WORDS);

        --========================================== Wires ======================================--
        --! Next value of the 'marker' register
        --! feedback style to support both sync and async resset options
        signal nx_marker                  : std_logic_vector(marker'range);
        signal in_fire, out_fire, is_full : boolean;
        signal pout_valid_o, sin_ready_o  : boolean;
        signal pout_array                 : t_pout_data_arr;
        signal sin_data_arr               : T_sin_channels(0 to G_CHANNELS - 1);
        --=======================================================================================--
    begin
        is_full    <= marker(marker'high) = '1';
        sin_ready  <= '1' when sin_ready_o else '0';
        pout_valid <= '1' when pout_valid_o else '0';
        in_fire    <= sin_valid = '1' and sin_ready_o;
        out_fire   <= pout_ready = '1' and pout_valid_o;

        assert FALSE report LF & "SIPO instance parameters:" --
        & LF & "  G_IN_W           " & integer'image(G_IN_W) --
        & LF & "  G_N              " & integer'image(G_N) --
        & LF & "  G_CHANNELS       " & integer'image(G_CHANNELS) --
        & LF & "  G_ASYNC_RSTN     " & boolean'image(G_ASYNC_RSTN) --
        & LF & "  G_BIGENDIAN      " & boolean'image(G_BIGENDIAN) --
        & LF & "  G_PIPELINED      " & boolean'image(G_PIPELINED) --
        & LF & "  G_WITH_LAST      " & boolean'image(G_WITH_LAST) --
        & LF & "  G_CLEAR_INVALIDS " & boolean'image(G_CLEAR_INVALIDS) --
        severity NOTE;

        assert not (                    -- invalid or not supported parameter combinations
            (G_PIPELINED and (G_WITH_LAST or G_CLEAR_INVALIDS) ) or --
            (G_CLEAR_INVALIDS and not G_WITH_LAST) --
        )
        report "Parameter combination is not supported!"
        severity FAILURE;

        --==================================== GEN_SPLIT_SIN ====================================--
        GEN_SPLIT_SIN : for i in sin_data_arr'range generate
            sin_data_arr(i) <= sin_data((i + 1) * G_IN_W - 1 downto i * G_IN_W);
        end generate;

        --==================================== GEN_PIPELINED ====================================--
        GEN_PIPELINED : if G_PIPELINED generate
            pout_valid_o <= is_full;
            nx_marker    <= (1 => '1', others => '0') when in_fire and out_fire else
                            INIT_MARKER when out_fire else
                            '0' & marker(0 to BUFF_WORDS - 1) when in_fire else
                            marker;
            process(clk)
            begin
                if rising_edge(clk) then
                    if in_fire then
                        -- shift-in sin_data into buff_array (little-endian)
                        for ch in 0 to G_CHANNELS - 1 loop
                            data(ch) <= data(ch)(1 to BUFF_WORDS - 1) & sin_data_arr(ch);
                        end loop;
                    end if;
                end if;
            end process;
            GEN_POUT_ARRAY_CH : for ch in 0 to G_CHANNELS - 1 generate
                GEN_POUT_ARRAY : for i in 0 to BUFF_WORDS - 1 generate
                    pout_array(ch)(i) <= data(ch)(i);
                end generate;
            end generate;
        end generate GEN_PIPELINED;
        -- Passthrough version, i.e., passes the last input to output
        -- there will be a combinational path from input data to output data
        GEN_NOT_PIPELINED : if not G_PIPELINED generate
            signal valids : std_logic_vector(0 to BUFF_WORDS - 1);
        begin
            nx_marker <= INIT_MARKER when out_fire else
                         '0' & marker(0 to BUFF_WORDS - 1) when in_fire else
                         marker;
            GEN_WITH_LAST : if G_WITH_LAST generate
                pout_valid_o <= (is_full or sin_last = '1') and sin_valid = '1';
            end generate;
            GEN_WITHOUT_LAST : if not G_WITH_LAST generate
                pout_valid_o <= is_full and sin_valid = '1';
            end generate;

            GEN_CHANNELS : for ch in 0 to G_CHANNELS - 1 generate
                process(clk)
                begin
                    if rising_edge(clk) then
                        if in_fire then
                            for i in 0 to BUFF_WORDS - 1 loop
                                if marker(i) = '1' then
                                    data(ch)(i) <= sin_data_arr(ch);
                                end if;
                            end loop;
                        end if;
                    end if;
                end process;
                GEN_POUT_ARRAY : for i in 0 to BUFF_WORDS - 1 generate
                    pout_array(ch)(i) <= bitmask(valids(i), data(ch)(i)) or --
                                         bitmask(marker(i), sin_data_arr(ch));
                end generate;
                GEN_POUT_ARRAY_ZERO_EMPTY : if G_CLEAR_INVALIDS generate
                    pout_array(ch)(BUFF_WORDS) <= bitmask(marker(BUFF_WORDS), sin_data_arr(ch));
                end generate;
                GEN_POUT_ARRAY_NOT_ZERO_EMPTY : if not G_CLEAR_INVALIDS generate -- else generate
                    pout_array(ch)(BUFF_WORDS) <= sin_data_arr(ch);
                end generate;
            end generate;
            GEN_ZERO_INVALIDS : if G_WITH_LAST and G_CLEAR_INVALIDS generate
                process(marker)
                    variable any_marker : std_logic_vector(0 to BUFF_WORDS - 1);
                begin
                    any_marker(BUFF_WORDS - 1) := marker(BUFF_WORDS);
                    for i in BUFF_WORDS - 2 downto 0 loop
                        any_marker(i) := marker(i + 1) or any_marker(i + 1);
                    end loop;

                    valids <= any_marker;
                end process;
            end generate;
            GEN_NO_ZERO_INVALIDS : if not G_WITH_LAST or not G_CLEAR_INVALIDS generate -- else generate
                valids <= not marker(0 to BUFF_WORDS - 1);
            end generate;
        end generate GEN_NOT_PIPELINED;

        --====================================== pout_data ======================================--
        GEN_CHANNELS : for ch in 0 to G_CHANNELS - 1 generate
            GEN_POUT_DATA : for i in 0 to G_N - 1 generate
                alias pd : std_logic_vector is pout_data((i + ch * G_N + 1) * G_IN_W - 1 downto (i + ch * G_N) * G_IN_W);
            begin
                GEN_BIGENDIAN : if G_BIGENDIAN generate
                    pd <= pout_array(ch)(G_N - 1 - i);
                end generate;
                GEN_NOT_BIGENDIAN : if not G_BIGENDIAN generate -- else generate
                    pd <= pout_array(ch)(i);
                end generate;
            end generate;
        end generate;

        --===================================== G_WITH_LAST =====================================--
        GEN_WITH_LAST : if G_WITH_LAST and not G_PIPELINED generate
            sin_ready_o <= pout_ready = '1' or (sin_last /= '1' and not is_full);
            pout_last   <= sin_last;
        end generate;
        GEN_WITHOUT_LAST : if not G_WITH_LAST or G_PIPELINED generate -- else generate
            sin_ready_o <= pout_ready = '1' or not is_full;
            pout_last   <= '0';
        end generate;

        --==================================== Regs w/ reset ====================================--
        GEN_proc_SYNC_RST : if not G_ASYNC_RSTN generate
            process(clk)
            begin
                if rising_edge(clk) then
                    if rst = '1' then
                        marker <= INIT_MARKER;
                    else
                        marker <= nx_marker;
                    end if;
                end if;
            end process;
        end generate GEN_proc_SYNC_RST;
        GEN_proc_ASYNC_RSTN : if G_ASYNC_RSTN generate
            process(clk, rst)
            begin
                if rst = '0' then
                    marker <= INIT_MARKER;
                elsif rising_edge(clk) then
                    marker <= nx_marker;
                end if;
            end process;
        end generate GEN_proc_ASYNC_RSTN;

    end generate GEN_NONTRIVIAL;
end architecture;
