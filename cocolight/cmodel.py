import cffi


class CModel:
    def __init__(self, sources) -> None:
        self.sources = sources
        self.lib = None
        self.ffi = None
        self.func_prototypes: list[str] = []

    def compile(self):
        ffibuilder = cffi.FFI()
        cdefs = "\n".join(self.func_prototypes)
        ffibuilder.cdef(cdefs)
        ffibuilder.set_source(
            "_cmodel",
            cdefs,
            sources=self.sources,
            library_dirs=[],
            #  libraries = []
        )

        ffibuilder.compile(verbose=True, tmpdir=".")
        # from _cmodel import ffi, lib

        # self.ffi = ffi
        # self.lib = lib
