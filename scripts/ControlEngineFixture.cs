// Owned qualification window: no network, personal files or system configuration.
using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Drawing;

public static class ControlEngineFixture {
    [STAThread]
    public static void Main() {
        Application.EnableVisualStyles();
        var form = new Form { Text = "JARVIS Engine Qualification - Owned Fixture", Width = 680, Height = 280 };
        var panel = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown,
            WrapContents = false, Padding = new Padding(16) };
        var description = new Label { Text = "Owned test window. Nothing is sent to another application.", AutoSize = true };
        var editor = new TextBox { Name = "FixtureDraft", AccessibleName = "Fixture draft", Width = 600,
            Multiline = true, Height = 65 };
        var button = new Button { Name = "ApplyFixture", AccessibleName = "Apply fixture", Text = "Apply fixture", Width = 150 };
        var status = new Label { AccessibleName = "Fixture result", Text = "Waiting for fixture input", AutoSize = true };
        var state = new Dictionary<string, object> { { "draft", "" }, { "applied_text", "" }, { "click_count", 0 } };
        var report = Path.Combine(Application.StartupPath, "desktop-engine-fixture.json");
        Action save = () => File.WriteAllText(report, new JavaScriptSerializer().Serialize(state));
        editor.TextChanged += (sender, args) => { state["draft"] = editor.Text; save(); };
        button.Click += (sender, args) => {
            state["click_count"] = (int)state["click_count"] + 1;
            state["applied_text"] = editor.Text;
            status.Text = "Applied: " + editor.Text;
            save();
        };
        panel.Controls.Add(description); panel.Controls.Add(editor); panel.Controls.Add(button); panel.Controls.Add(status);
        form.Controls.Add(panel);
        save();
        Application.Run(form);
    }
}
