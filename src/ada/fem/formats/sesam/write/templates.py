# IDENT (SIF 4.1.2): SLEVEL, SELTYP, SELMOD.
#   SLEVEL 1  -- a first-level super element. Basic elements are level 0 and a super
#               element's level is one above its sub elements, so an assembly is 2 or more.
#               adapy writes a single first-level super element, never an assembly, so 1.
#   SELTYP    -- the super element number. Sesam's file naming ties it to the deck's own
#               name: T<n>.FEM carries super element n, and Presel matches the two. So this
#               is a field, not a constant, and the writer names the file to agree with it.
#   SELMOD 3  -- a 3-dimensional model.
top_level_fem_str = """IDENT     1.00000000E+00  {seltyp:.8E}  3.00000000E+00  0.00000000E+00
DATE      1.00000000E+00  0.00000000E+00  4.00000000E+00  7.20000000E+01
        DATE:     {date_str}         TIME:          {clock_str}
        PROGRAM:  ADA python          VERSION:       Not Applicable
        COMPUTER: X86 Windows         INSTALLATION:
        USER:     {user}            ACCOUNT:     \n"""

sestra_header_inp_str = """HEAD
COMM
COMM    Created by: ADA
COMM
COMM    Date : {date_str}   Time : {clock_str}   User : {user}
COMM"""

sestra_eig_inp_str = """
COMM  CHCK ANTP MSUM MOLO STIF RTOP LBCK      PILE CSING     SINGM
CMAS    0.   2.   1.   1.   0.   0.   0.        0.
COMM
COMM                 WCOR THCK
ELOP                   1.   0.
COMM
COMM  ITYP
ITOP    {supnr}.
COMM
COMM  PREFIX
INAM  {name}
COMM
COMM  PREFIX FORMAT
LNAM  {name} UNFORMATTED
COMM
COMM  PREFIX FORMAT
RNAM  {name} NORSAM
COMM
COMM  SEL1 SEL2 SEL3 SEL4 SEL5 SEL6 SEL7 SEL8
RSEL    1.   0.   0.   0.   0.   0.   1.   0.
COMM
COMM  RTRA
RETR    3.
COMM
COMM  ENR                                                              SHIFT
EIGA   {modes}.                                                         0.
COMM
COMM  SELT
IDTY    1.
COMM
COMM  IMAS IDAM ISST
DYMA    1.   0.   0.
Z"""

sestra_static_inp_str = """
COMM  CHCK ANTP MSUM MOLO STIF RTOP LBCK      PILE CSING     SINGM
CMAS    0.   1.   1.   0.   0.   0.   0.        0.
COMM
COMM            ORDR                          CACH MFRWORK
SOLM              0.                            0.        0.
COMM
COMM                 WCOR THCK
ELOP                   1.   0.
COMM
COMM  ITYP
ITOP   {supnr}.
COMM
COMM  PREFIX
INAM  {name}
COMM
COMM  PREFIX FORMAT
LNAM  {name} UNFORMATTED
COMM
COMM  PREFIX FORMAT
RNAM  {name} NORSAM
COMM
COMM  SEL1 SEL2 SEL3 SEL4 SEL5 SEL6 SEL7 SEL8
RSEL    1.   0.   0.   0.   0.   0.   1.   0.
COMM
COMM  RTRA
RETR    3.
COMM
COMM  SELT
IDTY   {supnr}.
COMM
COMM  IMAS IDAM ISST
DYMA    1.   0.   0.
Z"""
