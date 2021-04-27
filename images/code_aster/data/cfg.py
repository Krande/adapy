def configure(self):
    opts = self.options

    opts.parallel = True
    opts.enable_petsc = True
    opts.petsc_libs = "petsc HYPRE ml"

    opts.maths_libs = "openblas superlu"
    opts.enable_homard = True
    opts.with_prog_metis = True
    opts.with_prog_gmsh = True
    opts.with_prog_homard = True
    opts.with_prog_xmgrace = True

    self.env.append_value("LIB_METIS", ("parmetis"))
    self.env.append_value("LIB_SCOTCH", ("ptscotch", "ptscotcherr", "ptscotcherrexit", "ptesmumps"))
