using System.Windows.Controls;
using CommunityToolkit.Mvvm.ComponentModel;
using ShetMitraAdmin.Views;

namespace ShetMitraAdmin.ViewModels;

#nullable enable

public partial class MainViewModel : ObservableObject
{
    [ObservableProperty]
    private int selectedTabIndex;

    [ObservableProperty]
    private UserControl? currentView;

    public MainViewModel()
    {
        SelectedTabIndex = 0;
        CurrentView = new DashboardView();
    }

    partial void OnSelectedTabIndexChanged(int value)
    {
        CurrentView = value switch
        {
            0 => new DashboardView(),
            1 => new FarmerDetailView(),
            2 => new AlertsView(),
            3 => new AnalyticsPlaceholderView(),
            4 => new PriceAnalysisView(),
            5 => new BeltIntelligenceView(),
            6 => new DroneOperationsPlaceholderView(),
            _ => new DashboardView()
        };
    }
}
