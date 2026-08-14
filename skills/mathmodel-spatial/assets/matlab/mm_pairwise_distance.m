function D = mm_pairwise_distance(X)
%MM_PAIRWISE_DISTANCE Euclidean distances without a toolbox dependency.
arguments
    X double {mustBeFinite, mustBeReal}
end
n = size(X, 1);
D = zeros(n, n);
for i = 1:n
    for j = (i + 1):n
        value = norm(X(i, :) - X(j, :), 2);
        D(i, j) = value;
        D(j, i) = value;
    end
end
end
