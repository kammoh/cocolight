CWD=$(shell pwd)

TOPLEVEL_LANG=vhdl
VHDL_SOURCES =$(CWD)/sqrt.vhdl
SIM=ghdl

TOPLEVEL=sqrt
MODULE=tb_sqrt

ifeq ($(SIM),ghdl)
EXTRA_ARGS=--std=08
endif
ifeq ($(SIM),questa)
SIM_ARGS=-t 1ps
endif

include $(shell cocotb-config --makefiles)/Makefile.sim