using CommunityToolkit.Mvvm.ComponentModel;

namespace ShetMitraAdmin.Models;

#nullable enable

/// <summary>
/// Input model bound to the inline "Enter Manually" form on the Alerts screen.
/// When saved, this becomes an <c>agent_verified</c> variety_responses row.
/// </summary>
public partial class ManualVarietyEntry : ObservableObject
{
    [ObservableProperty] private string variety = "";
    [ObservableProperty] private double? acres;
    [ObservableProperty] private string? notes;
}
