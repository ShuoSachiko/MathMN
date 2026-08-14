%% Reproducible MATLAB starter for one modeling subproblem
% Copy this file into code/problemN.m, then replace the example calculation.

clearvars;
close all;
clc;
rng(2025, "twister");

scriptDir = fileparts(mfilename("fullpath"));
projectRoot = fileparts(scriptDir);
resultDir = fullfile(projectRoot, "results");
figureDir = fullfile(projectRoot, "figures");
if ~isfolder(resultDir), mkdir(resultDir); end
if ~isfolder(figureDir), mkdir(figureDir); end

% Example deterministic calculation. Replace with the actual model.
x = linspace(0, 2*pi, 200).';
y = sin(x);
resultTable = table(x, y, "VariableNames", ["x", "y"]);
writetable(resultTable, fullfile(resultDir, "problem_result.csv"));
save(fullfile(resultDir, "problem_workspace.mat"), "x", "y");

figure("Color", "white");
plot(x, y, "LineWidth", 1.5);
xlabel("x");
ylabel("y");
grid on;
exportgraphics(gcf, fullfile(figureDir, "fig_problem_result.pdf"), ...
    "ContentType", "vector");

fprintf("RESULT rows=%d min=%.8f max=%.8f\n", height(resultTable), min(y), max(y));

