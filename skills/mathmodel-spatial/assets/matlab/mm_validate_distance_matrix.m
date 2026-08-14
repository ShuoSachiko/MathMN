function report = mm_validate_distance_matrix(D, tolerance)
%MM_VALIDATE_DISTANCE_MATRIX Validate metric invariants deterministically.
arguments
    D double {mustBeFinite, mustBeReal}
    tolerance (1, 1) double {mustBeNonnegative} = 1e-9
end
if size(D, 1) ~= size(D, 2)
    error("MathModelAgent:Spatial:NonSquare", "Distance matrix must be square.");
end
n = size(D, 1);
report.nonnegative = all(D(:) >= -tolerance);
report.zeroDiagonal = all(abs(diag(D)) <= tolerance);
report.symmetric = all(abs(D - D.') <= tolerance, "all");
report.triangle = true;
report.maxTriangleViolation = 0;
for i = 1:n
    for j = 1:n
        for k = 1:n
            violation = D(i, k) - D(i, j) - D(j, k);
            report.maxTriangleViolation = max(report.maxTriangleViolation, violation);
            if violation > tolerance
                report.triangle = false;
            end
        end
    end
end
report.pass = report.nonnegative && report.zeroDiagonal && report.symmetric && report.triangle;
end
