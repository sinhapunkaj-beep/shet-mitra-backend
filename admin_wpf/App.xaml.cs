using System.IO;
using System.Windows;
using Microsoft.Extensions.Configuration;
using ShetMitraAdmin.Services;

namespace ShetMitraAdmin;

#nullable enable

public partial class App : Application
{
    public static IConfiguration? Configuration { get; private set; }
    public static SupabaseService? Supabase { get; private set; }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        var builder = new ConfigurationBuilder()
            .SetBasePath(Directory.GetCurrentDirectory())
            .AddJsonFile("appsettings.json", optional: true, reloadOnChange: false);

        Configuration = builder.Build();

        var url = Configuration["Supabase:Url"] ?? "";
        var key = Configuration["Supabase:AnonKey"] ?? "";
        var internalBaseUrl = Configuration["Internal:BaseUrl"] ?? "http://localhost:8000";

        Supabase = new SupabaseService(url, key, internalBaseUrl);
    }
}
