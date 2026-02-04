FEA Verification Report
=======================

This interactive report presents the results of eigenvalue analysis verification tests across
multiple FEA software packages supported by ADA.

.. raw:: html

    <style>
        .fea-report-container {
            width: 100%;
            min-height: 800px;
            border: 1px solid #ddd;
            border-radius: 4px;
            overflow: hidden;
            margin: 20px 0;
        }
        .fea-report-container iframe {
            width: 100%;
            height: 900px;
            border: none;
        }
        .fea-report-placeholder {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 400px;
            background: #f5f5f5;
            border-radius: 4px;
            padding: 40px;
            text-align: center;
        }
        .fea-report-placeholder h3 {
            margin-bottom: 16px;
            color: #333;
        }
        .fea-report-placeholder p {
            color: #666;
            max-width: 600px;
        }
        .fea-report-placeholder code {
            background: #e0e0e0;
            padding: 2px 6px;
            border-radius: 3px;
        }
    </style>

    <div class="fea-report-container">
        <iframe id="fea-report-frame" src="../../_static/fea-report/index.html" 
                title="FEA Verification Report"
                onload="this.style.display='block'"
                onerror="showPlaceholder()">
        </iframe>
        <div id="fea-report-placeholder" class="fea-report-placeholder" style="display: none;">
            <h3>📊 FEA Verification Report</h3>
            <p>
                The interactive report is not available. To generate it, run:
            </p>
            <p>
                <code>python tests/fem/verification_report/build_verification_report.py --export-static-web</code>
            </p>
        </div>
    </div>

    <script>
        // Check if iframe loaded successfully
        const iframe = document.getElementById('fea-report-frame');
        const placeholder = document.getElementById('fea-report-placeholder');
        
        iframe.onerror = function() {
            iframe.style.display = 'none';
            placeholder.style.display = 'flex';
        };
        
        // Also check after a short delay in case the file doesn't exist
        setTimeout(function() {
            try {
                // Try to access iframe content - will fail for missing files or cross-origin
                if (!iframe.contentDocument && !iframe.contentWindow.document) {
                    throw new Error('Cannot access iframe');
                }
            } catch(e) {
                // If we get here, the iframe might not have loaded properly
                // Show placeholder only if iframe appears empty
                if (iframe.clientHeight < 100) {
                    iframe.style.display = 'none';
                    placeholder.style.display = 'flex';
                }
            }
        }, 2000);
    </script>


About This Report
-----------------

The FEA Verification Report compares eigenvalue analysis results across different:

- **FEA Software**: Code_Aster, CalculiX, Abaqus, Sesam
- **Element Types**: Line (beam), Shell, and Solid elements
- **Element Orders**: 1st and 2nd order elements
- **Mesh Types**: Triangular/Tetrahedral vs Quadrilateral/Hexahedral

Test Geometry
~~~~~~~~~~~~~

The verification tests use a standard IPE400 cantilever beam:

- **Length**: 3.0 m
- **Material**: S420 Carbon Steel
- **Boundary Conditions**: Fixed at one end (cantilever)
- **Analysis Type**: Eigenvalue (natural frequency) analysis

Results Interpretation
~~~~~~~~~~~~~~~~~~~~~~

For each analysis configuration, the report shows:

- Eigenvalue results for multiple vibration modes
- Comparison across different FEA solvers
- Percentage differences from reference values

Generating the Report
~~~~~~~~~~~~~~~~~~~~~

To regenerate the verification report with your local FEA installations:

.. code-block:: bash

    # Navigate to the verification report directory
    cd tests/fem/verification_report
    
    # Run the verification tests and generate static web report
    python build_verification_report.py --export-static-web
    
    # Or with specific options
    python build_verification_report.py --overwrite --execute --export-static-web

This will:

1. Run eigenvalue analyses using all available FEA software on your system
2. Collect and compare results across configurations
3. Generate static web files in ``docs/_static/fea-report/``

.. note::

    The verification tests require at least one FEA solver to be installed and configured.
    Supported solvers include: Code_Aster, CalculiX, Abaqus, and Sesam.
