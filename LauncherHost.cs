using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class LauncherHost
{
    private static readonly object Sync = new object();
    private static Process launcherProcess;
    private static bool stopping;

    private static string ProjectRoot
    {
        get { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar); }
    }

    public static int Main()
    {
        Console.OutputEncoding = Encoding.UTF8;
        Console.Title = "微信 AI 助手";
        AppDomain.CurrentDomain.ProcessExit += delegate { StopLauncherTree(); };
        Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs args)
        {
            args.Cancel = true;
            StopLauncherTree();
        };

        string launcherPath = Path.Combine(ProjectRoot, "launcher.py");
        if (!File.Exists(launcherPath))
        {
            Console.WriteLine("找不到 launcher.py，请把微信AI助手.exe放在项目根目录。\n");
            Console.ReadKey();
            return 1;
        }

        string pythonPath = Path.Combine(ProjectRoot, "backend", ".venv", "Scripts", "python.exe");
        if (!File.Exists(pythonPath))
        {
            pythonPath = "python.exe";
        }

        try
        {
            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = pythonPath,
                Arguments = Quote(launcherPath),
                WorkingDirectory = ProjectRoot,
                UseShellExecute = false,
                CreateNoWindow = false
            };
            launcherProcess = Process.Start(startInfo);
            if (launcherProcess == null)
            {
                Console.WriteLine("无法启动启动器。\n");
                Console.ReadKey();
                return 1;
            }
            launcherProcess.WaitForExit();
            return launcherProcess.ExitCode;
        }
        catch (Exception error)
        {
            Console.WriteLine("启动失败：" + error.Message);
            Console.ReadKey();
            return 1;
        }
        finally
        {
            StopLauncherTree();
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static void StopLauncherTree()
    {
        lock (Sync)
        {
            if (stopping || launcherProcess == null || launcherProcess.HasExited)
            {
                return;
            }
            stopping = true;
        }

        try
        {
            ProcessStartInfo killInfo = new ProcessStartInfo
            {
                FileName = "taskkill.exe",
                Arguments = "/PID " + launcherProcess.Id + " /T /F",
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using (Process killer = Process.Start(killInfo))
            {
                if (killer != null)
                {
                    killer.WaitForExit(5000);
                }
            }
        }
        catch
        {
            try { launcherProcess.Kill(); } catch { }
        }
    }
}
