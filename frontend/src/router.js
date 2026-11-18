import { createRouter, createWebHistory } from 'vue-router'
import Home from './pages/Home.vue'
import Events from './pages/Events.vue'
import Redeem from './pages/Redeem.vue'
import Batches from './pages/Batches.vue'
import Quarantine from './pages/Quarantine.vue'
import Settings from './pages/Settings.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/events', component: Events },
    { path: '/redeem', component: Redeem },
    { path: '/batches', component: Batches },
    { path: '/quarantine', component: Quarantine },
    { path: '/settings', component: Settings },
  ],
})
