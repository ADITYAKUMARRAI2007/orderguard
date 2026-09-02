import { BrowserRouter, Routes, Route } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Layout } from "@/components/Layout";
import { Mission } from "@/pages/Mission";
import { Shop } from "@/pages/Shop";
import { Connectors } from "@/pages/Connectors";
import { AttackLab } from "@/pages/AttackLab";
import { Evidence } from "@/pages/Evidence";
import { Features } from "@/pages/Features";
import { EvalPage } from "@/pages/Eval";

export default function App() {
  return (
    <TooltipProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Mission />} />
            <Route path="shop" element={<Shop />} />
            <Route path="connectors" element={<Connectors />} />
            <Route path="attack-lab" element={<AttackLab />} />
            <Route path="evidence" element={<Evidence />} />
            <Route path="features" element={<Features />} />
            <Route path="eval" element={<EvalPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  );
}
