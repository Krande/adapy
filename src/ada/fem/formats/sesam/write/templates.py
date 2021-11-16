top_level_fem_str = """IDENT     1.00000000E+00  1.00000000E+00  3.00000000E+00  0.00000000E+00
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
