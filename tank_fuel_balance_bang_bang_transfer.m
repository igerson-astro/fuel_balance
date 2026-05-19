%% Tank fuel balance from FLOPS-style mission CSV
% This script reads a mission CSV containing:
%   - Time [hr]
%   - Turbogen Fuel Flow [lb/hr]
%   - Propulsor Fuel Flow [lb/hr]
%
% Fuel architecture:
%   - FWD tank directly feeds all turbogen fuel demand plus half propulsor demand.
%   - AFT tank directly feeds the other half of propulsor demand.
%   - AFT can transfer fuel to FWD.
%
% Transfer objective:
%   Size the AFT-to-FWD transfer rate each timestep so that FWD and AFT
%   remain at nearly the same fill fraction, subject to min/max transfer limits.
%
% Sign convention:
%   transfer_lbhr > 0 means AFT transfers fuel to FWD.

clear; clc; close all;

%% User inputs
%missionFile = "flops_mission_with_component_breakdown_mach1.7mission.csv";
%missionFile = "flops_mission_with_component_breakdown_maxendurance.csv";
missionFile = "flops_mission_with_component_breakdown_reference.csv";

% If this script is not in the same folder as the CSV, set missionFile to a
% full path, or uncomment this file picker:
% [f, p] = uigetfile({'*.csv','CSV files (*.csv)'; '*.*','All files'}, 'Select mission file');
% missionFile = fullfile(p, f);

% Initial tank fuel quantities. These are also used as default tank
% capacities for fill-fraction balancing. If actual usable tank capacities
% differ from initial load, edit the capacity variables below.
fwdInitial_kg = 1604;
aftInitial_kg = 1053;
kgToLb = 2.20462262185;

fwdInitial_lb = fwdInitial_kg * kgToLb;
aftInitial_lb = aftInitial_kg * kgToLb;

% Tank capacities used for fill-percent calculation.
% Default: initial fuel load equals 100 percent full.
fwdCapacity_lb = fwdInitial_lb;
aftCapacity_lb = aftInitial_lb;

% Transfer limits from AFT to FWD.
% transferMin_lbhr only applies when a positive transfer is commanded.
% Example: if transferMin_lbhr = 100 and the balance command is 40 lb/hr,
% the actual transfer becomes 100 lb/hr. Leave at 0 for no lower limit.
transferMin_lbhr = 100;
transferMax_lbhr = 101;

% If true, transfer during a timestep is limited by current AFT fuel.
% If false, tanks are allowed to go negative so fuel shortfall is visible.
clampTransferToAvailableAftFuel = false;

% Integration option for flow data over each timestep.
% "trapezoid" uses average of endpoint flows. "previous" uses flow at k-1.
integrationMethod = "trapezoid";

%% Load mission data
if ~isfile(missionFile)
    error('Mission file not found: %s', missionFile);
end

mission = readtable(missionFile, 'VariableNamingRule', 'preserve');

% Robust column matching. Add aliases here if future files use different names.
timeCol = findColumn(mission, ["Time [hr]", "Time_hr", "Time hr", "Time"]);
tgFlowCol = findColumn(mission, ["Turbogen Fuel Flow", "TurbogenFuelFlow", "TG Fuel Flow"]);
propFlowCol = findColumn(mission, ["Propulsor Fuel Flow", "PropulsorFuelFlow", "Prop Fuel Flow"]);

time_hr = mission.(timeCol);
tgFuelFlow_lbhr = mission.(tgFlowCol);
propFuelFlow_lbhr = mission.(propFlowCol);

validateattributes(time_hr, {'numeric'}, {'vector', 'nonempty', 'finite'});
validateattributes(tgFuelFlow_lbhr, {'numeric'}, {'vector', 'numel', numel(time_hr)});
validateattributes(propFuelFlow_lbhr, {'numeric'}, {'vector', 'numel', numel(time_hr)});

% Force column vectors.
time_hr = time_hr(:);
tgFuelFlow_lbhr = tgFuelFlow_lbhr(:);
propFuelFlow_lbhr = propFuelFlow_lbhr(:);

if any(diff(time_hr) < 0)
    error('Time column must be monotonically increasing.');
end

n = numel(time_hr);

%% Allocate results
fwdFuel_lb = nan(n, 1);
aftFuel_lb = nan(n, 1);
totalFuel_lb = nan(n, 1);
fwdFillFrac = nan(n, 1);
aftFillFrac = nan(n, 1);

transfer_lbhr = zeros(n, 1);          % transfer rate applied over interval ending at row k
transferFuel_lb = zeros(n, 1);        % transfer mass over interval ending at row k
fwdDirectDemand_lbhr = zeros(n, 1);   % turbogen + half propulsor, before transfer
aftDirectDemand_lbhr = zeros(n, 1);   % half propulsor, before transfer
fwdNetBurn_lbhr = zeros(n, 1);        % after transfer credit
aftNetBurn_lbhr = zeros(n, 1);        % after transfer debit

fwdFuel_lb(1) = fwdInitial_lb;
aftFuel_lb(1) = aftInitial_lb;
totalFuel_lb(1) = fwdFuel_lb(1) + aftFuel_lb(1);
fwdFillFrac(1) = fwdFuel_lb(1) / fwdCapacity_lb;
aftFillFrac(1) = aftFuel_lb(1) / aftCapacity_lb;

%% Time-marching fuel balance
for k = 2:n
    dt_hr = time_hr(k) - time_hr(k-1);

    if dt_hr == 0
        fwdFuel_lb(k) = fwdFuel_lb(k-1);
        aftFuel_lb(k) = aftFuel_lb(k-1);
        totalFuel_lb(k) = totalFuel_lb(k-1);
        fwdFillFrac(k) = fwdFuel_lb(k) / fwdCapacity_lb;
        aftFillFrac(k) = aftFuel_lb(k) / aftCapacity_lb;
        continue;
    end

    switch lower(integrationMethod)
        case "trapezoid"
            tgRate = 0.5 * (tgFuelFlow_lbhr(k-1) + tgFuelFlow_lbhr(k));
            propRate = 0.5 * (propFuelFlow_lbhr(k-1) + propFuelFlow_lbhr(k));
        case "previous"
            tgRate = tgFuelFlow_lbhr(k-1);
            propRate = propFuelFlow_lbhr(k-1);
        otherwise
            error('Unknown integrationMethod: %s', integrationMethod);
    end

    % Direct tank demands before transfer.
    fwdDirectRate = tgRate + 0.5 * propRate;
    aftDirectRate = 0.5 * propRate;

    fwdDirectDemand_lbhr(k) = fwdDirectRate;
    aftDirectDemand_lbhr(k) = aftDirectRate;

    % Fuel remaining after direct demand only, before transfer.
    fwdPreTransfer = fwdFuel_lb(k-1) - fwdDirectRate * dt_hr;
    aftPreTransfer = aftFuel_lb(k-1) - aftDirectRate * dt_hr;

    % Choose transfer mass x [lb] so end-of-step fill fractions match:
    %   (fwdPreTransfer + x) / fwdCapacity =
    %   (aftPreTransfer - x) / aftCapacity
    % Solving gives:
    transferNeeded_lb = (fwdCapacity_lb * aftPreTransfer - aftCapacity_lb * fwdPreTransfer) / ...
                        (fwdCapacity_lb + aftCapacity_lb);
    transferNeeded_lbhr = transferNeeded_lb / dt_hr;

    % AFT-to-FWD only. If transferNeeded is negative, exact balancing would
    % require FWD-to-AFT transfer, which this architecture does not allow.
    if transferNeeded_lbhr > 0
        transferCommand_lbhr = min(max(transferNeeded_lbhr, transferMin_lbhr), transferMax_lbhr);
    else
        transferCommand_lbhr = 0;
    end

    if clampTransferToAvailableAftFuel
        transferCommand_lbhr = min(transferCommand_lbhr, aftPreTransfer / dt_hr);
    end

    transfer_lbhr(k) = transferCommand_lbhr;
    transferFuel_lb(k) = transferCommand_lbhr * dt_hr;

    % Apply transfer.
    fwdFuel_lb(k) = fwdPreTransfer + transferFuel_lb(k);
    aftFuel_lb(k) = aftPreTransfer - transferFuel_lb(k);

    fwdNetBurn_lbhr(k) = fwdDirectRate - transferCommand_lbhr;
    aftNetBurn_lbhr(k) = aftDirectRate + transferCommand_lbhr;

    totalFuel_lb(k) = fwdFuel_lb(k) + aftFuel_lb(k);
    fwdFillFrac(k) = fwdFuel_lb(k) / fwdCapacity_lb;
    aftFillFrac(k) = aftFuel_lb(k) / aftCapacity_lb;
end

%% Results table
results = table();
results.(timeCol) = time_hr;
results.TurbogenFuelFlow_lbhr = tgFuelFlow_lbhr;
results.PropulsorFuelFlow_lbhr = propFuelFlow_lbhr;
results.FwdDirectDemand_lbhr = fwdDirectDemand_lbhr;
results.AftDirectDemand_lbhr = aftDirectDemand_lbhr;
results.AftToFwdTransfer_lbhr = transfer_lbhr;
results.AftToFwdTransferFuel_lb = transferFuel_lb;
results.FwdNetBurn_lbhr = fwdNetBurn_lbhr;
results.AftNetBurn_lbhr = aftNetBurn_lbhr;
results.FwdFuel_lb = fwdFuel_lb;
results.AftFuel_lb = aftFuel_lb;
results.TotalFuel_lb = totalFuel_lb;
results.FwdFillPercent = 100 * fwdFillFrac;
results.AftFillPercent = 100 * aftFillFrac;
results.FillPercentDifference = 100 * (fwdFillFrac - aftFillFrac);

[missionFolder, missionBase, ~] = fileparts(missionFile);
if missionFolder == ""
    missionFolder = pwd;
end
resultsFile = fullfile(missionFolder, missionBase + "_tank_balance_results.csv");
writetable(results, resultsFile);

%% Command-window summary
fprintf('\nFuel balance complete.\n');
fprintf('Input file:   %s\n', missionFile);
fprintf('Results file: %s\n', resultsFile);
fprintf('Initial FWD:  %.1f lb, Initial AFT: %.1f lb\n', fwdInitial_lb, aftInitial_lb);
fprintf('Final FWD:    %.1f lb, Final AFT:   %.1f lb\n', fwdFuel_lb(end), aftFuel_lb(end));
fprintf('Final FWD %%:  %.2f %%, Final AFT %%: %.2f %%\n', 100*fwdFillFrac(end), 100*aftFillFrac(end));
fprintf('Minimum FWD:  %.1f lb, Minimum AFT: %.1f lb\n', min(fwdFuel_lb), min(aftFuel_lb));
fprintf('Max transfer: %.1f lb/hr\n', max(transfer_lbhr));
fprintf('Max Fill %% Diff:  %.2f %%\n\n',max(abs(results.FillPercentDifference)));

%% Plots
figure('Name', 'Tank Fuel Levels');
plot(time_hr, fwdFuel_lb, 'LineWidth', 1.6); hold on;
plot(time_hr, aftFuel_lb, 'LineWidth', 1.6);
plot(time_hr, totalFuel_lb, '--', 'LineWidth', 1.6);
yline(0, ':', 'Zero fuel');
grid on;
xlabel('Time [hr]');
ylabel('Fuel [lb]');
title('Tank Fuel Levels vs Time');
legend('FWD tank', 'AFT tank', 'Total fuel', 'Zero fuel', 'Location', 'best');

figure('Name', 'Tank Fill Percentages');
plot(time_hr, 100*fwdFillFrac, 'LineWidth', 1.6); hold on;
plot(time_hr, 100*aftFillFrac, 'LineWidth', 1.6);
yline(0, ':', 'Empty');
grid on;
xlabel('Time [hr]');
ylabel('Fill [% of specified tank capacity]');
title('Tank Fill Percentages vs Time');
legend('FWD fill %', 'AFT fill %', 'Empty', 'Location', 'best');

figure('Name', 'Fuel Flow Demands');
plot(time_hr, tgFuelFlow_lbhr, 'LineWidth', 1.6); hold on;
plot(time_hr, propFuelFlow_lbhr, 'LineWidth', 1.6);
plot(time_hr, tgFuelFlow_lbhr + propFuelFlow_lbhr, '--', 'LineWidth', 1.6);
grid on;
xlabel('Time [hr]');
ylabel('Fuel flow [lb/hr]');
title('Fuel Flow Demands vs Time');
legend('Turbogen demand', 'Propulsor demand', 'Total demand', 'Location', 'best');

figure('Name', 'AFT to FWD Transfer');
plot(time_hr, transfer_lbhr, 'LineWidth', 1.6);
grid on;
xlabel('Time [hr]');
ylabel('AFT-to-FWD transfer [lb/hr]');
title('Transfer Command vs Time');

%% Local helper function
function colName = findColumn(tbl, aliases)
    names = string(tbl.Properties.VariableNames);
    normalizedNames = normalizeName(names);
    normalizedAliases = normalizeName(string(aliases));

    for a = 1:numel(normalizedAliases)
        idx = find(normalizedNames == normalizedAliases(a), 1, 'first');
        if ~isempty(idx)
            colName = names(idx);
            return;
        end
    end

    % Fallback: contains search after normalization.
    for a = 1:numel(normalizedAliases)
        idx = find(contains(normalizedNames, normalizedAliases(a)), 1, 'first');
        if ~isempty(idx)
            colName = names(idx);
            return;
        end
    end

    error('Could not find required column. Tried aliases: %s\nAvailable columns: %s', ...
          strjoin(string(aliases), ', '), strjoin(names, ', '));
end

function out = normalizeName(in)
    out = lower(regexprep(string(in), '[^a-zA-Z0-9]', ''));
end
