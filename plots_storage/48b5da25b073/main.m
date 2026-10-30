
try
    graphics_toolkit('gnuplot');
catch
end
set(0, 'defaultfigurevisible', 'off');

% --- USER SCRIPT START ---
t = 0:0.1:10; y = sin(t); disp("Hello from public web"); plot(t, y);
% --- USER SCRIPT END ---

% Auto export any active figures
try
    figs = get(0, 'children');
    for idx = 1:length(figs)
        fname = sprintf('fig_%02d.png', idx);
        print(figs(idx), fname, '-dpng', '-r130');
    end
catch e
    fprintf(stderr, 'Plot Export Error: %s\n', e.message);
end
